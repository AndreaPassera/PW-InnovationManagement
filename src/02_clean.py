import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# relative paths in the config are resolved from the repository root
os.chdir(ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
from sqlalchemy import func

from epo.tipdata.patstat import PatstatClient
from epo.tipdata.patstat.database.models import (
    TLS201_APPLN,
    TLS206_PERSON,
    TLS207_PERS_APPLN,
    TLS224_APPLN_CPC,
)

import config as C
from pw_utils import (
    applicant_type, available_columns, clean_country, clean_patent_text, fetch_by_ids, fractional_weights,
    get_model, legal_event_flags, normalise_name, to_group,
)

ESTABLISHED_MIN_FAMILIES = 5
COUNTRY_FALLBACK_TO_OFFICE = True
CLIENT_GROUP = C.CLIENT
ABBREVIATIONS = {"UNIV": "UNIVERSITY", "INST": "INSTITUTE", "INTL": "INTERNATIONAL", "NATL": "NATIONAL",
                 "TECHNOL": "TECHNOLOGY"}
NON_ANCHOR_TYPES = {"university", "individual"}
GENERIC_WORDS = {
    "AND", "OF", "THE", "FOR", "DE", "DEL", "DI", "DES", "DU", "LA", "LE",
    "TECHNOLOGY", "TECHNOLOGIES", "TECH", "SYSTEM", "SYSTEMS", "SOLUTIONS", "SERVICES", "INDUSTRIES", "INDUSTRIAL",
    "INDUSTRY", "INTERNATIONAL", "GLOBAL", "GROUP", "HOLDING", "HOLDINGS", "ENTERPRISES", "ASSOCIATES", "PARTNERS",
    "VENTURES", "COMPANY", "PRODUCTS", "MANUFACTURING", "ENGINEERING", "DESIGN", "INNOVATION", "INNOVATIONS",
    "DEVELOPMENT", "MANAGEMENT", "APPLIED", "ADVANCED", "NEW", "DIGITAL", "DATA", "COMMUNICATIONS", "CONTROL",
    "INSTRUMENTS", "SENSORS", "MACHINERY", "MACHINES", "MATERIALS", "DYNAMICS", "ELECTRONICS", "ELECTRONIC",
    "ELECTRIC", "ELECTRICAL", "MECHANICAL", "POWER", "ENERGY", "AEROSPACE", "DEFENSE", "DEFENCE", "RESEARCH",
    "INSTITUTE", "LABORATORY", "LABORATORIES", "LABS", "SCIENCE", "SCIENCES", "SCIENTIFIC", "CENTER", "CENTRE",
    "NATIONAL", "GOVERNMENT", "MINISTRY", "DEPARTMENT", "UNDERWATER", "SUBSEA", "SUBMARINE", "MARINE", "MARITIME",
    "OCEAN", "OCEANIC", "OCEANOGRAPHIC", "SEA", "WATER", "NAVAL", "OFFSHORE", "SHIPBUILDING", "SHIPYARD", "SHIPYARDS",
    "VEHICLE", "VEHICLES", "ROBOT", "ROBOTS", "ROBOTIC", "ROBOTICS", "AUTONOMOUS", "UNMANNED", "DRONE", "DRONES",
    "USA", "US", "AMERICA", "AMERICAN", "CHINA", "CHINESE", "JAPAN", "KOREA", "EUROPE", "EUROPEAN", "UNITED",
    "STATES", "STATE",
}

END_TYPES = [("revoked", r"\bREVOK|\bREVOCATION\b(?!.*BEFORE THE COURT)|PATENT REVOKED"),
             ("refused", r"REJECT|REFUS"),
             ("withdrawn", r"WITHDRAW|ABANDON|DISCONTINU"),
             ("lapsed", r"LAPSE|CEASED|CESSATION|TERMINATION OF PATENT RIGHT|EXPIR|NON-PAYMENT|FAILURE TO PAY|NOT PAYING")]
NOT_AN_END = (r"REVIVAL|REINSTATE|RE-ESTABLISH|RESTORATION|CLAIM OR COUNTERCLAIM|OPT-OUT|INTENTION|INTENDED|REQUEST FOR|"
              r"NOTICE OF|NOTIFICATION OF REASON|DECISION OF GRANT OR REJECTION|APPEAL|OPPOSITION (?:WAS )?(?:REJECTED|WITHDRAWN)|"
              r"TRANSFER WITHDRAWN|INFORMATION DELETED|MODIFIED|REMAND|DOMESTIC PRIORITY|EXPIRATION OF 19TH MONTH")
COUNTRY_LEVEL = r"CONTRACTING STATE|^[A-Z]{2}: |INVALIDATED EUROPEAN PATENT|INVALIDATION OF EXTENSION|TRANSLATION"

US_STATES = {"ALABAMA", "ALASKA", "ARIZONA", "ARKANSAS", "CALIFORNIA", "COLORADO", "CONNECTICUT", "DELAWARE", "FLORIDA",
             "GEORGIA", "HAWAII", "IDAHO", "ILLINOIS", "INDIANA", "IOWA", "KANSAS", "KENTUCKY", "LOUISIANA", "MAINE",
             "MARYLAND", "MASSACHUSETTS", "MICHIGAN", "MINNESOTA", "MISSISSIPPI", "MISSOURI", "MONTANA", "NEBRASKA",
             "NEVADA", "NEW HAMPSHIRE", "NEW JERSEY", "NEW MEXICO", "NEW YORK", "NORTH CAROLINA", "NORTH DAKOTA", "OHIO",
             "OKLAHOMA", "OREGON", "PENNSYLVANIA", "RHODE ISLAND", "SOUTH CAROLINA", "SOUTH DAKOTA", "TENNESSEE", "TEXAS",
             "UTAH", "VERMONT", "VIRGINIA", "WASHINGTON", "WEST VIRGINIA", "WISCONSIN", "WYOMING", "DISTRICT OF COLUMBIA"}
COUNTRY_WORDS = {"UNITED KINGDOM", "UNITED STATES", "UNITED STATES OF AMERICA", "USA", "NORWAY", "SWEDEN", "DENMARK",
                 "FINLAND", "GERMANY", "FRANCE", "ITALY", "SPAIN", "PORTUGAL", "NETHERLANDS", "BELGIUM", "SWITZERLAND",
                 "AUSTRIA", "IRELAND", "SCOTLAND", "ENGLAND", "CANADA", "AUSTRALIA", "NEW ZEALAND", "JAPAN", "CHINA",
                 "KOREA", "REPUBLIC OF KOREA", "SINGAPORE", "TAIWAN", "HONG KONG", "INDIA", "ISRAEL", "SAUDI ARABIA",
                 "UNITED ARAB EMIRATES", "BRAZIL", "MEXICO", "RUSSIA", "RUSSIAN FEDERATION", "POLAND", "TURKEY",
                 "SOUTH AFRICA", "LUXEMBOURG", "CAYMAN ISLANDS", "BERMUDA", "BRITISH VIRGIN ISLANDS", "CYPRUS", "MALTA"}
OWNER_CATEGORIES = [
    ("procedural", r"TRANSFER TO EXAMINER|TRANSFER OF A CASE|RECONSIDERATION|TRANSFER WITHDRAWN|REQUEST FOR"),
    ("security", r"SECURITY|\bLIEN\b|PLEDGE|RELEASE BY SECURED|RELEASE OF SECURITY|PATENT RELEASE|RELEASE OF .*LIEN"),
    ("licence", r"LICEN[CS]E"),
    ("correction", r"CORRECTIVE|NUNC PRO TUNC|CORRECTION"),
    ("merger", r"MERGER"),
    ("rename", r"CHANGE OF NAME|NAME CHANGE|CHANGE OF ADDRESS"),
]
UNKNOWN_OWNER = "UNKNOWN (not recorded)"


def fetch(patstat, make_query, ids, columns=None):
    out = fetch_by_ids(patstat, make_query, ids)
    return pd.DataFrame(columns=columns) if out.empty and columns else out


def to_date(s):
    # 9999-12-31 is PATSTAT's placeholder for a missing date
    d = pd.to_datetime(pd.Series(s), errors="coerce")
    return d.where(d.dt.year < C.MISSING_YEAR)


def months_between(a, b):
    return (b - a).dt.days / 30.4375


def col_or_empty(df, name, fill=""):
    return df.get(name, pd.Series(fill, index=df.index))


def load_step0_outputs():
    raw = C.OUT_STEP0 / "raw"
    sector = pd.read_csv(C.OUT_STEP0 / "sector_families.csv")
    apps = pd.read_csv(raw / "applications.csv.gz")
    titles = pd.read_csv(raw / "titles.csv.gz")
    abstracts = pd.read_csv(raw / "abstracts.csv.gz")
    cpc = pd.read_csv(raw / "cpc.csv.gz")
    assert sector["docdb_family_id"].is_unique, "the sector table must have one row per family"
    family_ids = sector["docdb_family_id"].astype("int64").tolist()
    appln_ids = apps["appln_id"].astype("int64").unique().tolist()
    print(f"env {C.ENV} | IPR {C.IPR_TYPE} | sector families: {len(family_ids):,} | applications: {len(appln_ids):,}")
    return sector, apps, titles, abstracts, cpc, family_ids, appln_ids


def build_families(sector, apps, titles, abstracts):
    offices = apps.groupby("docdb_family_id")["appln_auth"]
    apps["is_pct"] = apps["appln_auth"].eq("WO") & apps["appln_kind"].astype(str).str.startswith("W")

    families = sector[["docdb_family_id", "family_year", "is_truncated", "first_office", "has_en_abstract",
                       "block_combo"]].merge(
        pd.DataFrame({
            "inpadoc_family_id": apps.groupby("docdb_family_id")["inpadoc_family_id"].first(),
            "n_applications": apps.groupby("docdb_family_id")["appln_id"].nunique(),
            "docdb_family_size": apps.groupby("docdb_family_id")["docdb_family_size"].max(),
            "nb_citing_docdb_fam": apps.groupby("docdb_family_id")["nb_citing_docdb_fam"].max(),
            "granted_any": apps.assign(g=apps["granted"].eq("Y")).groupby("docdb_family_id")["g"].any(),
            "via_pct": apps.groupby("docdb_family_id")["is_pct"].any(),
            "n_offices": offices.nunique(),
            "n_national_offices": offices.agg(lambda s: s[~s.isin(["WO", "EP"])].nunique()),
            "has_ep": offices.agg(lambda s: s.eq("EP").any()),
            "offices": offices.agg(lambda s: " ".join(sorted(s.unique()))),
            "first_publn_year": apps.groupby("docdb_family_id")["earliest_publn_year"].min(),
        }).reset_index(),
        on="docdb_family_id", how="left",
    )
    families["is_international"] = families["n_offices"] > 1

    en_title = (titles[(titles["appln_title_lg"] == "en") & titles["appln_title"].fillna("").str.strip().ne("")]
                .drop_duplicates("docdb_family_id")[["docdb_family_id", "appln_title"]]
                .rename(columns={"appln_title": "title_en"}))
    any_title = titles.drop_duplicates("docdb_family_id")[["docdb_family_id", "appln_title"]].rename(
        columns={"appln_title": "title_any"})
    en_abs = (abstracts[(abstracts["appln_abstract_lg"] == "en") & abstracts["appln_abstract"].fillna("").str.strip().ne("")]
              .drop_duplicates("docdb_family_id")[["docdb_family_id", "appln_abstract"]]
              .rename(columns={"appln_abstract": "abstract_en"}))
    families = (families.merge(en_title, on="docdb_family_id", how="left")
                        .merge(any_title, on="docdb_family_id", how="left")
                        .merge(en_abs, on="docdb_family_id", how="left"))
    families["title"] = families["title_en"].fillna(families["title_any"])
    families["clean_abstract"] = families["abstract_en"].apply(clean_patent_text)
    families = families.drop(columns="title_any")

    # application-level twin of the family table: the sector is chosen by family, then all its applications are kept
    applications = apps.merge(families[["docdb_family_id", "is_truncated"]], on="docdb_family_id", how="left")
    applications = applications.rename(columns={"is_truncated": "is_truncated_family"})
    applications["is_truncated_appln"] = applications["appln_filing_year"] > C.LAST_COMPLETE_YEAR
    applications["in_appln_window"] = applications["appln_filing_year"].between(C.YEAR_MIN, C.YEAR_MAX)
    applications["is_granted"] = applications["granted"].eq("Y")
    print(f"applications: {len(applications):,} | per family: mean {len(applications) / len(families):.2f} "
          f"| outside {C.YEAR_MIN}-{C.YEAR_MAX} by filing year: {(~applications['in_appln_window']).sum():,}")
    print(f"families: {len(families):,} | English abstract: {families['abstract_en'].notna().sum():,} "
          f"| granted: {families['granted_any'].mean():.1%} | via PCT: {families['via_pct'].mean():.1%} "
          f"| international: {families['is_international'].mean():.1%} | truncated: {families['is_truncated'].sum():,}")
    return families, applications


def cpc_tables(cpc, families):
    family_cpc = cpc[["docdb_family_id", "cpc"]].drop_duplicates().copy()
    family_cpc["subclass"] = family_cpc["cpc"].str[:4]
    family_cpc["main_group"] = family_cpc["cpc"].str.extract(r"^([A-Z]\d{2}[A-Z]\d+/)", expand=False)
    # section Y tags an application domain, not a technology
    family_cpc["is_tagging"] = family_cpc["cpc"].str.startswith("Y")
    families = families.merge(
        family_cpc[~family_cpc["is_tagging"]].groupby("docdb_family_id")["subclass"].nunique().rename("n_subclasses"),
        on="docdb_family_id", how="left")
    # weight uses every subclass, weight_tech leaves the Y section out
    family_subclass = fractional_weights(family_cpc, "docdb_family_id", "subclass")[["docdb_family_id", "subclass", "weight"]]
    family_subclass = family_subclass.merge(
        fractional_weights(family_cpc[~family_cpc["is_tagging"]], "docdb_family_id", "subclass")[
            ["docdb_family_id", "subclass", "weight"]].rename(columns={"weight": "weight_tech"}),
        on=["docdb_family_id", "subclass"], how="left")
    app_cpc = cpc.assign(subclass=cpc["cpc"].str[:4])
    application_subclass = fractional_weights(app_cpc, "appln_id", "subclass")[
        ["appln_id", "docdb_family_id", "subclass", "weight"]]
    return families, family_cpc, family_subclass, application_subclass


def wipo_fields(patstat, db, tls, appln_ids):
    if not (C.FETCH_WIPO_FIELDS and tls["tls230"] is not None):
        return
    t230, t901 = tls["tls230"], tls["tls901"]
    wf = fetch(patstat, lambda ch: db.query(TLS201_APPLN.docdb_family_id, t230.appln_id, t230.techn_field_nr, t230.weight)
               .join(t230, t230.appln_id == TLS201_APPLN.appln_id)
               .filter(TLS201_APPLN.appln_id.in_(ch)), appln_ids)
    if wf.empty:
        return
    wf[["appln_id", "techn_field_nr", "weight"]].to_csv(C.OUT_STEP1 / "application_wipo_fields.csv", index=False)
    # weights sum to 1 per application; rescale so that every family sums to 1
    wf = wf.groupby(["docdb_family_id", "techn_field_nr"], as_index=False)["weight"].sum()
    wf["weight"] = wf["weight"] / wf.groupby("docdb_family_id")["weight"].transform("sum")
    if t901 is not None:
        names = pd.DataFrame(patstat.df(db.query(t901.techn_field_nr, t901.techn_field,
                                                 t901.techn_sector).distinct())).drop_duplicates("techn_field_nr")
        wf = wf.merge(names, on="techn_field_nr", how="left")
    wf.to_csv(C.OUT_STEP1 / "family_wipo_fields.csv", index=False)


def publication_tables(patstat, db, tls, apps, appln_ids, families):
    publications = pd.DataFrame()
    t211 = tls["tls211"]
    if not (C.FETCH_PUBLICATIONS and t211 is not None):
        return publications, families
    cols = available_columns(t211, ["pat_publn_id", "appln_id", "publn_auth", "publn_kind", "publn_date",
                                    "publn_first_grant"])
    publications = fetch(patstat, lambda ch: db.query(*cols).filter(t211.appln_id.in_(ch)), appln_ids)
    publications = publications.merge(apps[["appln_id", "docdb_family_id"]], on="appln_id", how="left")
    publications["publn_year"] = pd.to_datetime(publications["publn_date"], errors="coerce").dt.year
    publications.loc[publications["publn_year"] >= C.MISSING_YEAR, "publn_year"] = np.nan

    if "publn_first_grant" in publications:
        grants = publications[publications["publn_first_grant"].astype(str).str.strip().eq("Y")]
        first_grant = grants.groupby("docdb_family_id")["publn_year"].min().rename("first_grant_year").reset_index()
        families = families.merge(first_grant, on="docdb_family_id", how="left")
        families["grant_lag"] = families["first_grant_year"] - families["family_year"]
        print(f"families with a grant publication: {families['first_grant_year'].notna().sum():,} "
              f"| median grant lag: {families['grant_lag'].median()} years")
    print(f"publications: {len(publications):,}")
    return publications, families


def family_citations(patstat, db, tls, families, family_ids):
    t228 = tls["tls228"]

    def citing_years(ids):
        if not len(ids):
            return pd.DataFrame(columns=["docdb_family_id", "family_year"])
        return fetch(
            patstat,
            lambda ch: db.query(TLS201_APPLN.docdb_family_id,
                                func.min(TLS201_APPLN.earliest_filing_year).label("family_year"))
            .filter(TLS201_APPLN.docdb_family_id.in_(ch))
            .filter(TLS201_APPLN.earliest_filing_year != C.MISSING_YEAR)
            .group_by(TLS201_APPLN.docdb_family_id),
            ids,
        )

    if t228 is None:
        print("family citation table not available: family citations skipped")
        fwd = pd.DataFrame(columns=["citing_family_id", "docdb_family_id"])
        bwd = pd.DataFrame(columns=["docdb_family_id", "cited_family_id"])
    else:
        cols = ["docdb_family_id", "cited_docdb_family_id"]
        fwd = fetch(patstat, lambda ch: db.query(t228.docdb_family_id, t228.cited_docdb_family_id)
                    .filter(t228.cited_docdb_family_id.in_(ch)), family_ids, cols)
        bwd = fetch(patstat, lambda ch: db.query(t228.docdb_family_id, t228.cited_docdb_family_id)
                    .filter(t228.docdb_family_id.in_(ch)), family_ids, cols)
        fwd = fwd.rename(columns={"docdb_family_id": "citing_family_id", "cited_docdb_family_id": "docdb_family_id"})
        bwd = bwd.rename(columns={"cited_docdb_family_id": "cited_family_id"})

    fwd = fwd[fwd["citing_family_id"] != fwd["docdb_family_id"]].drop_duplicates()
    bwd = bwd[bwd["cited_family_id"] != bwd["docdb_family_id"]].drop_duplicates()

    years = citing_years(fwd["citing_family_id"].unique()).rename(
        columns={"docdb_family_id": "citing_family_id", "family_year": "citing_year"})
    fwd = (fwd.merge(years, on="citing_family_id", how="left")
              .merge(families[["docdb_family_id", "family_year"]], on="docdb_family_id", how="left"))
    fwd["lag"] = fwd["citing_year"] - fwd["family_year"]
    fwd["within_5y"] = fwd["lag"].between(0, 5)
    fwd["citing_in_sector"] = fwd["citing_family_id"].isin(family_ids)
    bwd["cited_in_sector"] = bwd["cited_family_id"].isin(family_ids)

    for name, s in {"fwd_cits": fwd.groupby("docdb_family_id").size(),
                    "fwd_cits_5y": fwd[fwd["within_5y"]].groupby("docdb_family_id").size(),
                    "fwd_cits_in_sector": fwd[fwd["citing_in_sector"]].groupby("docdb_family_id").size(),
                    "bwd_cits": bwd.groupby("docdb_family_id").size()}.items():
        families = families.merge(s.rename(name), on="docdb_family_id", how="left")
        families[name] = families[name].fillna(0).astype(int)
    families["fwd_window_complete"] = families["family_year"] + 5 <= C.LAST_COMPLETE_YEAR
    print(f"family citation links: forward {len(fwd):,} | backward {len(bwd):,} "
          f"| forward inside the sector: {fwd['citing_in_sector'].mean() if len(fwd) else 0:.1%}")
    return fwd, bwd, families


def citing_cpc(patstat, db, fwd, bwd):
    if not (C.FETCH_CITATION_CPC and (len(fwd) or len(bwd))):
        return
    other_ids = pd.unique(pd.concat([fwd["citing_family_id"], bwd["cited_family_id"]]).astype("int64"))
    cit_cpc = fetch(
        patstat,
        lambda ch: db.query(TLS201_APPLN.docdb_family_id, TLS224_APPLN_CPC.cpc_class_symbol)
        .join(TLS224_APPLN_CPC, TLS201_APPLN.appln_id == TLS224_APPLN_CPC.appln_id)
        .filter(TLS201_APPLN.docdb_family_id.in_(ch))
        .distinct(),
        other_ids,
    )
    if cit_cpc.empty:
        return
    cit_cpc["subclass"] = cit_cpc["cpc_class_symbol"].str.replace(" ", "", regex=False).str[:4]
    cit_cpc = cit_cpc.loc[~cit_cpc["subclass"].str.startswith("Y"), ["docdb_family_id", "subclass"]].drop_duplicates()
    cit_cpc.to_csv(C.OUT_STEP1 / "cited_citing_cpc.csv", index=False)
    print(f"CPC subclasses of cited/citing families: {len(cit_cpc):,} rows")


def persons(patstat, db, role_filter, appln_ids):
    person_cols = [TLS201_APPLN.docdb_family_id, TLS201_APPLN.appln_id, TLS201_APPLN.appln_auth,
                   TLS207_PERS_APPLN.applt_seq_nr, TLS207_PERS_APPLN.invt_seq_nr,
                   TLS206_PERSON.person_id, TLS206_PERSON.person_name,
                   TLS206_PERSON.psn_id, TLS206_PERSON.psn_name, TLS206_PERSON.psn_sector,
                   TLS206_PERSON.han_id, TLS206_PERSON.han_name, TLS206_PERSON.person_ctry_code]
    # NUTS region of the address, available for European persons only
    person_cols += [getattr(TLS206_PERSON, c) for c in ("nuts", "nuts_level") if hasattr(TLS206_PERSON, c)]
    return fetch(
        patstat,
        lambda ch: db.query(*person_cols)
        .select_from(TLS201_APPLN)
        .join(TLS207_PERS_APPLN, TLS201_APPLN.appln_id == TLS207_PERS_APPLN.appln_id)
        .join(TLS206_PERSON, TLS207_PERS_APPLN.person_id == TLS206_PERSON.person_id)
        .filter(TLS201_APPLN.appln_id.in_(ch))
        .filter(role_filter)
        .distinct(),
        appln_ids,
    )


def normalise_full(name):
    if pd.isna(name) or not str(name).strip():
        return "UNKNOWN"
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii") or str(name)
    s = s.upper().replace("&", " AND ")
    s = re.sub(r"\bA\s*/\s*S\b", "AS", s)
    s = re.sub(r"\bS\s+P\s+A\b", "SPA", s)
    s = re.sub(r"\bS\s+R\s+L\b", "SRL", s)
    s = re.sub(r"[.,']", "", s)
    s = " ".join(ABBREVIATIONS.get(t, t) for t in s.split())
    return normalise_name(s) or "UNKNOWN"


def is_generic_only(name):
    tokens = str(name).split()
    return bool(tokens) and all(t in GENERIC_WORDS for t in tokens)


def build_anchor_map(name_table, established_min_families=None):
    info = {r.name_norm: (r.applicant_type, int(r.n_families)) for r in name_table.itertuples(index=False)}
    tokens = {n: tuple(n.split()) for n in info}
    anchors = {tokens[n]: n for n in info
               if n != "UNKNOWN" and info[n][0] not in NON_ANCHOR_TYPES and not is_generic_only(n)}
    resolved = {}

    def resolve(name):
        if name in resolved:
            return resolved[name]
        result, (kind, n_fam) = name, info[name]
        established = established_min_families is not None and n_fam >= established_min_families
        if name != "UNKNOWN" and kind not in NON_ANCHOR_TYPES and not established:
            words = tokens[name]
            # longest proper prefix first, then follow the chain of anchors
            for k in range(len(words) - 1, 0, -1):
                anchor = anchors.get(words[:k])
                if anchor is not None:
                    result = resolve(anchor)
                    break
        resolved[name] = result
        return result

    return {n: resolve(n) for n in info}


def add_country(df):
    df["country"] = df["person_ctry_code"].apply(clean_country)
    df["country_source"] = np.where(df["country"].notna(), "person", "unknown")
    if COUNTRY_FALLBACK_TO_OFFICE:
        office = df["appln_auth"].astype("string").str.strip()
        use_office = df["country"].isna() & office.notna() & ~office.isin(["EP", "WO", ""])
        df.loc[use_office, "country"] = office[use_office]
        df.loc[use_office, "country_source"] = "filing_office"
    return df


def harmonise_applicants(appl_raw):
    appl = appl_raw.copy()
    for c in ["psn_name", "han_name", "person_name"]:
        appl[c] = appl[c].astype("string").str.strip().replace("", pd.NA)
    appl["base_name"] = appl["psn_name"].fillna(appl["han_name"]).fillna(appl["person_name"]).fillna("")

    # layer 1: normalisation, once per distinct name
    norm_map = {n: normalise_full(n) for n in appl["base_name"].unique()}
    appl["name_norm"] = appl["base_name"].map(norm_map)
    appl["type_row"] = [applicant_type(s, n) for s, n in zip(appl["psn_sector"], appl["name_norm"])]

    # layer 2: anchor-prefix merge
    name_table = (appl.groupby("name_norm")
                  .agg(applicant_type=("type_row", lambda v: v[v != "unknown"].mode().iat[0] if (v != "unknown").any() else "unknown"),
                       n_families=("docdb_family_id", "nunique"))
                  .reset_index())
    anchor_map = build_anchor_map(name_table, ESTABLISHED_MIN_FAMILIES)
    appl["name_harmonised"] = appl["name_norm"].map(anchor_map)
    print(f"Layer 2 (anchor-prefix merge): {sum(k != v for k, v in anchor_map.items()):,} names absorbed into an anchor")

    # layer 3: corporate groups
    appl["applicant"] = appl["name_harmonised"].apply(to_group).replace("", "UNKNOWN")
    appl["applicant_type"] = [applicant_type(s, n) for s, n in zip(appl["psn_sector"], appl["applicant"])]

    appl = add_country(appl)
    print("Applicant country source (rows):", appl["country_source"].value_counts().to_dict())

    audit = (appl.groupby(["applicant", "name_harmonised", "name_norm", "base_name"])
             .agg(n_families=("docdb_family_id", "nunique")).reset_index()
             .sort_values(["n_families", "applicant"], ascending=[False, True]))
    audit["merged_by_anchor"] = (audit["name_norm"] != audit["name_harmonised"]).astype(int)
    audit["merged_by_group"] = (audit["name_harmonised"] != audit["applicant"]).astype(int)
    audit.to_csv(C.OUT_STEP1 / "applicants_audit.csv", index=False)
    print(f"Audit: {audit['base_name'].nunique():,} source names -> {audit['name_norm'].nunique():,} normalised -> "
          f"{audit['name_harmonised'].nunique():,} harmonised -> {audit['applicant'].nunique():,} groups")
    return appl, anchor_map


def applicant_tables(appl, families, appln_ids):
    applicants = (
        appl.groupby(["docdb_family_id", "applicant"])
        .agg(applicant_type=("applicant_type", lambda s: s.mode().iat[0]),
             country=("country", lambda s: s.dropna().mode().iat[0] if s.dropna().size else pd.NA),
             country_source=("country_source", lambda s: "person" if (s == "person").any()
                             else ("filing_office" if (s == "filing_office").any() else "unknown")),
             raw_names=("base_name", lambda s: " | ".join(sorted(set(s)))))
        .reset_index()
    )
    applicants["is_client_group"] = applicants["applicant"].eq(CLIENT_GROUP)
    # equal shares among the applicants of a family (concentration indices)
    applicants["weight"] = 1 / applicants.groupby("docdb_family_id")["applicant"].transform("size")
    # country shares: only applicants with a known country share the family
    applicants = applicants.merge(
        fractional_weights(applicants, "docdb_family_id", "applicant", require="country")[
            ["docdb_family_id", "applicant", "weight"]].rename(columns={"weight": "country_weight"}),
        on=["docdb_family_id", "applicant"], how="left")
    by_application = fractional_weights(
        appl[["appln_id", "docdb_family_id", "person_id", "country"]], "appln_id", "person_id", require="country")
    applicants = applicants.merge(families[["docdb_family_id", "family_year", "is_truncated"]], on="docdb_family_id")
    applicants["first_year_in_sector"] = applicants.groupby("applicant")["family_year"].transform("min")

    collab = applicants.groupby("docdb_family_id").agg(
        n_applicant_groups=("applicant", "nunique"),
        types=("applicant_type", lambda s: frozenset(s)))
    collab["co_assigned"] = collab["n_applicant_groups"] > 1
    collab["firm_univ_collab"] = collab["types"].apply(lambda t: "firm" in t and bool(t & {"university", "government / non-profit"}))
    families = families.merge(collab.drop(columns="types").reset_index(), on="docdb_family_id", how="left")
    print(f"family x applicant rows: {len(applicants):,} | groups: {applicants['applicant'].nunique():,} "
          f"| raw names: {appl['base_name'].nunique():,} | co-assigned families: {families['co_assigned'].mean():.1%}")
    app_cols = ["appln_id", "docdb_family_id", "appln_auth", "applt_seq_nr", "person_id", "person_name", "psn_id", "psn_name",
                "han_id", "han_name", "name_norm", "name_harmonised", "applicant", "applicant_type", "country", "country_source"]
    application_applicants = (appl[[c for c in app_cols if c in appl.columns]]
                              .drop_duplicates(["appln_id", "person_id"])
                              .sort_values(["docdb_family_id", "appln_id"] + (["applt_seq_nr"] if "applt_seq_nr" in appl else [])))
    application_applicants["is_client_group"] = application_applicants["applicant"].eq(CLIENT_GROUP)
    print(f"application x applicant rows: {len(application_applicants):,} | applications with an applicant: "
          f"{application_applicants['appln_id'].nunique():,} of {len(appln_ids):,}")
    return applicants, by_application, application_applicants, families


def inventor_tables(patstat, db, appln_ids, applicants, families):
    if not C.FETCH_INVENTORS:
        return None, families
    inv_raw = add_country(persons(patstat, db, TLS207_PERS_APPLN.invt_seq_nr > 0, appln_ids))
    print("Inventor country source (rows):", inv_raw["country_source"].value_counts().to_dict())
    inv_raw["inventor"] = inv_raw["psn_name"].fillna(inv_raw["person_name"]).astype("string").str.upper().str.strip()
    nuts_cols = [c for c in ("nuts", "nuts_level") if c in inv_raw.columns]
    if nuts_cols:
        # keep the most detailed NUTS code of each family x inventor; the detail is read from the length of the
        # code (2 country, 4 NUTS 2, 5 NUTS 3) because nuts_level also marks codes completed from REGPAT
        inv_raw["nuts"] = inv_raw["nuts"].astype("string").str.strip().replace({"": pd.NA})
        inv_raw["_nuts_len"] = inv_raw["nuts"].str.len().fillna(0).astype(int)
        inv_raw = inv_raw.sort_values("_nuts_len", ascending=False, kind="stable").drop(columns="_nuts_len")
    inventors = inv_raw.drop_duplicates(["docdb_family_id", "inventor"])[
        ["docdb_family_id", "person_id", "psn_id", "inventor", "country", "country_source"] + nuts_cols]
    inventors["weight"] = 1 / inventors.groupby("docdb_family_id")["inventor"].transform("size")
    inventors = inventors.merge(
        fractional_weights(inventors, "docdb_family_id", "inventor", require="country")[
            ["docdb_family_id", "inventor", "weight"]].rename(columns={"weight": "country_weight"}),
        on=["docdb_family_id", "inventor"], how="left")
    inventors.to_csv(C.OUT_STEP1 / "inventors.csv", index=False)
    fractional_weights(inv_raw[["appln_id", "docdb_family_id", "person_id", "country"]], "appln_id", "person_id",
                       require="country").to_csv(C.OUT_STEP1 / "inventor_countries_by_application.csv", index=False)
    families = families.merge(inventors.groupby("docdb_family_id")["inventor"].nunique().rename("team_size"),
                              on="docdb_family_id", how="left")
    print(f"inventor rows: {len(inventors):,} | median team size: {families['team_size'].median()} "
          f"| unknown country: {inventors['country'].isna().mean():.1%}")
    return inventors, families


def legal_event_tables(patstat, db, tls, apps, appln_ids, families):
    events = pd.DataFrame()
    t231, t803 = tls["tls231"], tls["tls803"]
    if not (C.FETCH_LEGAL_EVENTS and t231 is not None):
        return events, families
    ev_cols = available_columns(t231, ["appln_id", "event_seq_nr", "event_auth", "event_code", "event_publn_date",
                                       "event_effective_date", "event_category_code", "event_category_title",
                                       "event_text", "fee_country", "fee_renewal_year", "lapse_country",
                                       "party_type", "party_new", "party_old"])
    events = fetch(patstat, lambda ch: db.query(*ev_cols).filter(t231.appln_id.in_(ch)), appln_ids)
    if events.empty:
        return events, families
    events = events.merge(apps[["appln_id", "docdb_family_id"]], on="appln_id", how="left")
    events["event_code"] = events["event_code"].astype(str).str.strip()
    codes = events.groupby(["event_auth", "event_code"]).size().rename("n").reset_index()
    if t803 is not None:
        c803 = available_columns(t803, ["event_auth", "event_code", "event_descr", "event_category_code",
                                        "event_category_title"])
        desc = pd.DataFrame(patstat.df(db.query(*c803).filter(t803.event_code.in_(codes["event_code"].unique().tolist()))))
        if not desc.empty:
            desc["event_code"] = desc["event_code"].astype(str).str.strip()
            codes = codes.merge(desc, on=[c for c in ["event_auth", "event_code"] if c in desc.columns], how="left")

    # flags are read from the code descriptions: the free text is empty for many offices (all CN events)
    desc_cols = [c for c in ["event_descr", "event_category_title"] if c in codes.columns]
    if desc_cols:
        events = events.drop(columns=[c for c in desc_cols if c in events.columns]).merge(
            codes[["event_auth", "event_code"] + desc_cols], on=["event_auth", "event_code"], how="left")
    text = (col_or_empty(events, "event_descr").fillna("").astype(str) + " | "
            + col_or_empty(events, "event_category_title").fillna("").astype(str) + " | "
            + col_or_empty(events, "event_text").fillna("").astype(str))
    flags = legal_event_flags(text).add_prefix("ev_")
    events = pd.concat([events, flags], axis=1)
    events.to_csv(C.OUT_STEP1 / "legal_events.csv.gz", index=False)

    fam_ev = events.groupby("docdb_family_id")[list(flags.columns)].any()
    fam_ev["n_legal_events"] = events.groupby("docdb_family_id").size()
    if "fee_renewal_year" in events:
        ry = pd.to_numeric(events["fee_renewal_year"], errors="coerce")
        fam_ev["max_renewal_year"] = ry.groupby(events["docdb_family_id"]).max()
    if "fee_country" in events:
        fc = events["fee_country"].astype(str).str.strip().replace({"": np.nan, "nan": np.nan, "None": np.nan})
        fam_ev["n_renewal_countries"] = fc.groupby(events["docdb_family_id"]).nunique()
    families = families.merge(fam_ev.reset_index(), on="docdb_family_id", how="left")
    flag_cols = list(flags.columns)
    families[flag_cols] = families[flag_cols].fillna(False).astype(bool)
    # active proxy: at least one application without an event that ends the right; rough, because a lapse
    # can concern a single validation country
    ended = events.groupby("appln_id")[["ev_lapse", "ev_withdrawal_refusal", "ev_revocation"]].any().any(axis=1)
    app_alive = apps[["appln_id", "docdb_family_id"]].assign(alive=~apps["appln_id"].map(ended).fillna(False).astype(bool))
    families = families.merge(app_alive.groupby("docdb_family_id")["alive"].any().rename("active_proxy").reset_index(),
                              on="docdb_family_id", how="left")
    print(f"legal events: {len(events):,} | families with events: {fam_ev.shape[0]:,}")
    return events, families


def life_cycle(patstat, db, apps, appln_ids, publications, events, applications, families):
    d201_cols = available_columns(TLS201_APPLN, ["appln_id", "earliest_filing_date", "earliest_publn_date", "appln_filing_date"])
    d201 = fetch(patstat, lambda ch: db.query(*d201_cols).filter(TLS201_APPLN.appln_id.in_(ch)), appln_ids, ["appln_id"])
    life = apps[["appln_id", "docdb_family_id", "appln_auth", "appln_kind", "appln_filing_date"]].drop_duplicates("appln_id").copy()
    life = life.merge(d201.drop(columns=[c for c in ["appln_filing_date"] if c in d201.columns]).drop_duplicates("appln_id"),
                      on="appln_id", how="left")
    life["appln_filing_date"] = to_date(life["appln_filing_date"])
    life["priority_date"] = to_date(col_or_empty(life, "earliest_filing_date", pd.NaT))
    life["priority_date"] = life["priority_date"].fillna(life["appln_filing_date"])
    life["first_publn_date"] = to_date(col_or_empty(life, "earliest_publn_date", pd.NaT))
    life = life.drop(columns=[c for c in ["earliest_filing_date", "earliest_publn_date"] if c in life.columns])
    life["is_pct_request"] = life["appln_auth"].eq("WO") & life["appln_kind"].astype(str).str.startswith("W")

    if not publications.empty and "publn_date" in publications:
        pub = publications.copy()
        pub["publn_date"] = to_date(pub["publn_date"])
        pub["publn_kind"] = col_or_empty(pub, "publn_kind").astype(str).str.strip()
        pub["publn_auth"] = col_or_empty(pub, "publn_auth").astype(str).str.strip()
        first_grant_flag = col_or_empty(pub, "publn_first_grant", "N").astype(str).str.strip().eq("Y")
        life = life.merge(pub.groupby("appln_id")["publn_date"].min().rename("first_publn_date_211"), on="appln_id", how="left")
        life["first_publn_date"] = life["first_publn_date"].fillna(life.pop("first_publn_date_211"))
        gr = pub[first_grant_flag].sort_values("publn_date").drop_duplicates("appln_id")
        life = life.merge(gr[["appln_id", "publn_date", "publn_kind"]].rename(
            columns={"publn_date": "grant_date", "publn_kind": "grant_kind"}), on="appln_id", how="left")
    else:
        life["grant_date"], life["grant_kind"] = pd.NaT, None
    life["grant_date"] = pd.to_datetime(life["grant_date"])

    # a publication cannot precede the priority: such dates are source errors and are set to missing
    anomalous = life["first_publn_date"] < life["priority_date"]
    life.loc[anomalous, "first_publn_date"] = pd.NaT
    print(f"first publication dates before the priority date, set to missing: {int(anomalous.sum())}")

    granted_flag = apps.drop_duplicates("appln_id").set_index("appln_id")["granted"].eq("Y")
    life["is_granted"] = life["appln_id"].map(granted_flag).fillna(False).astype(bool) | life["grant_date"].notna()

    life["expiry_date"] = (life["appln_filing_date"] + pd.DateOffset(years=20)).where(~life["is_pct_request"])

    if not events.empty:
        e = events.merge(apps[["appln_id", "appln_auth"]].drop_duplicates("appln_id"), on="appln_id", how="left").copy()
        e["date"] = to_date(col_or_empty(e, "event_effective_date", pd.NaT)).fillna(to_date(col_or_empty(e, "event_publn_date", pd.NaT)))
        as_of = to_date(col_or_empty(e, "event_publn_date", pd.NaT)).max()
        descr = col_or_empty(e, "event_descr").fillna("").astype(str).str.upper()
        e["end_type"] = None
        # the first pattern in the list wins, so they are applied in reverse
        for name, pat in END_TYPES[::-1]:
            e.loc[descr.str.contains(pat, regex=True), "end_type"] = name
        e.loc[descr.str.contains(NOT_AN_END, regex=True), "end_type"] = None
        country_level = (descr.str.contains(COUNTRY_LEVEL, regex=True)
                         | (e["appln_auth"].eq("EP") & e["event_auth"].astype(str).str.strip().ne("EP")))
        central = e[e["end_type"].notna() & ~country_level].sort_values("date").drop_duplicates("appln_id")
        life = life.merge(central[["appln_id", "date", "end_type"]].rename(columns={"date": "end_date"}), on="appln_id", how="left")
        # a granted application is not refused or withdrawn in the end: only lapse and revocation can follow a grant
        overruled = life["is_granted"] & life["end_type"].isin(["refused", "withdrawn"]) & ~(life["end_date"] > life["grant_date"])
        life.loc[overruled, ["end_date", "end_type"]] = [pd.NaT, None]
        lapsed_c = e[e["end_type"].notna() & country_level & e["appln_auth"].eq("EP")].copy()
        lapsed_c["country"] = col_or_empty(lapsed_c, "lapse_country").astype(str).str.strip()
        lapsed_c.loc[lapsed_c["country"].isin(["", "nan", "None"]), "country"] = lapsed_c["event_auth"].astype(str).str.strip()
        life = life.merge(lapsed_c.groupby("appln_id").agg(n_lapsed_countries=("country", "nunique"),
                                                            last_country_lapse_date=("date", "max")).reset_index(),
                          on="appln_id", how="left")
        ren = e[col_or_empty(e, "ev_renewal_fee", False).astype(bool)]
        life = life.merge(ren.groupby("appln_id").agg(last_renewal_date=("date", "max")).reset_index(), on="appln_id", how="left")
        if "fee_renewal_year" in ren:
            life = life.merge(pd.to_numeric(ren["fee_renewal_year"], errors="coerce").groupby(ren["appln_id"]).max()
                              .rename("max_renewal_year").reset_index(), on="appln_id", how="left")
    else:
        as_of = pd.Timestamp.today().normalize()
        for col in ["end_date", "last_country_lapse_date", "last_renewal_date"]:
            life[col] = pd.NaT
        life["end_type"], life["n_lapsed_countries"], life["max_renewal_year"] = None, np.nan, np.nan
    if "max_renewal_year" not in life:
        life["max_renewal_year"] = np.nan
    for col in ["end_date", "last_country_lapse_date", "last_renewal_date"]:
        life[col] = pd.to_datetime(life[col])
    life["n_lapsed_countries"] = life["n_lapsed_countries"].fillna(0).astype(int)

    ended = life["end_date"].notna() & (life["end_date"] <= as_of)
    expired = life["expiry_date"].notna() & (life["expiry_date"] <= as_of)
    life["status"] = np.select(
        [life["is_pct_request"], ended, expired, life["is_granted"],
         (as_of - life["appln_filing_date"]).dt.days > 10 * 365.25],
        ["PCT request", "ended: " + life["end_type"].fillna("").astype(str), "expired", "in force",
         "pending, no decision recorded"], default="pending")
    life["residual_life_years"] = np.where(life["status"].eq("in force"), (life["expiry_date"] - as_of).dt.days / 365.25, np.nan)
    life["months_priority_to_filing"] = months_between(life["priority_date"], life["appln_filing_date"]).round(1)
    life["months_filing_to_publn"] = months_between(life["appln_filing_date"], life["first_publn_date"]).round(1)
    life["years_filing_to_grant"] = ((life["grant_date"] - life["appln_filing_date"]).dt.days / 365.25).round(2)

    date_cols = ["priority_date", "appln_filing_date", "first_publn_date", "grant_date", "expiry_date",
                 "last_renewal_date", "end_date", "last_country_lapse_date"]
    life_cols = date_cols + ["grant_kind", "end_type", "n_lapsed_countries", "max_renewal_year", "status", "residual_life_years",
                             "months_priority_to_filing", "months_filing_to_publn", "years_filing_to_grant"]
    applications = applications.drop(columns=[c for c in life_cols if c in applications.columns and c != "appln_filing_date"])
    applications["appln_filing_date"] = to_date(applications["appln_filing_date"])
    applications = applications.merge(life[["appln_id"] + [c for c in life_cols if c != "appln_filing_date"]].rename(
        columns={"max_renewal_year": "max_renewal_year_appln"}), on="appln_id", how="left")

    # family summary: PCT requests are left out, they are not rights of their own
    lf = life[~life["is_pct_request"]]
    gf = life.groupby("docdb_family_id")
    grants = lf[lf["grant_date"].notna()].sort_values("grant_date")
    in_force = lf[lf["status"].eq("in force")]
    pending = lf[lf["status"].isin(["pending"])]
    fam_life = pd.DataFrame({
        "priority_date": gf["priority_date"].min(),
        "first_filing_date": gf["appln_filing_date"].min(),
        "first_publn_date": gf["first_publn_date"].min(),
        "first_grant_date": grants.groupby("docdb_family_id")["grant_date"].min(),
        "first_grant_office": grants.drop_duplicates("docdb_family_id").set_index("docdb_family_id")["appln_auth"],
        "last_grant_date": grants.groupby("docdb_family_id")["grant_date"].max(),
        "n_granted_offices": grants.groupby("docdb_family_id")["appln_auth"].nunique(),
        "n_in_force_offices": in_force.groupby("docdb_family_id")["appln_auth"].nunique(),
        "family_expiry_date": in_force.groupby("docdb_family_id")["expiry_date"].max(),
        "family_residual_life_years": in_force.groupby("docdb_family_id")["residual_life_years"].max(),
        "last_end_date": lf.assign(end=lf["end_date"].where(ended.loc[lf.index], lf["expiry_date"].where(expired.loc[lf.index])))
                           .groupby("docdb_family_id")["end"].max(),
    })
    fam_life.index.name = "docdb_family_id"
    fam_life["years_priority_to_first_grant"] = ((fam_life["first_grant_date"] - fam_life["priority_date"]).dt.days / 365.25).round(2)
    fam_life["n_granted_offices"] = fam_life["n_granted_offices"].fillna(0).astype(int)
    fam_life["n_in_force_offices"] = fam_life["n_in_force_offices"].fillna(0).astype(int)
    has_pending = pending.groupby("docdb_family_id").size().reindex(fam_life.index).notna()
    fam_life["family_status"] = np.select([fam_life["n_in_force_offices"] > 0, has_pending], ["in force", "pending"], default="dead")
    fam_life["family_end_date"] = fam_life["last_end_date"].where(fam_life["family_status"].eq("dead"))
    fam_life = fam_life.drop(columns="last_end_date").reset_index()
    families = families.drop(columns=[c for c in fam_life.columns if c in families.columns and c != "docdb_family_id"])
    families = families.merge(fam_life, on="docdb_family_id", how="left")

    return applications, families


def owner_name(party):
    # drop the trailing ", STATE" or ", XX" that US and DE records add
    if pd.isna(party) or not str(party).strip():
        return None
    s = re.sub(r"\s+", " ", str(party).upper()).strip()
    parts = [p.strip() for p in s.split(",")]
    while len(parts) > 1 and (parts[-1] in US_STATES or parts[-1] in COUNTRY_WORDS or re.fullmatch(r"[A-Z]{2}", parts[-1])
                              or re.fullmatch(r"\d{4,6}.*", parts[-1])):
        parts = parts[:-1]
    return ", ".join(parts)


def owner_group(name, anchor_map):
    if not name:
        return None
    norm = normalise_full(name)
    # some US records append the state without a comma: drop it when the shorter name is a known applicant
    for place in sorted(US_STATES | COUNTRY_WORDS, key=len, reverse=True):
        if norm.endswith(" " + place) and norm[: -len(place) - 1] in anchor_map:
            norm = norm[: -len(place) - 1]
            break
    return to_group(anchor_map.get(norm, norm)) or None


def ownership_tables(events, anchor_map, applicants, application_applicants, families):
    if events.empty:
        print("legal events not fetched: ownership changes skipped")
        return families
    t = events[events["ev_transfer"]].copy()
    t["date"] = to_date(col_or_empty(t, "event_effective_date", pd.NaT)).fillna(to_date(col_or_empty(t, "event_publn_date", pd.NaT)))
    text = (col_or_empty(t, "party_old").fillna("").astype(str) + " | "
            + col_or_empty(t, "event_descr").fillna("").astype(str)).str.upper()
    t["category"] = "assignment"
    for name, pat in OWNER_CATEGORIES[::-1]:
        t.loc[text.str.contains(pat, regex=True), "category"] = name
    t["new_owner_name"] = col_or_empty(t, "party_new", None).apply(owner_name)
    t["new_owner"] = t["new_owner_name"].map({n: owner_group(n, anchor_map) for n in t["new_owner_name"].dropna().unique()})
    filed_by = application_applicants.groupby("appln_id")["applicant"].agg(lambda s: set(s.dropna()))
    t["filed_by"] = t["appln_id"].map(filed_by.to_dict()).apply(lambda s: s if isinstance(s, set) else set())
    t["is_real_change"] = (t["category"].isin(["assignment", "merger"])
                           & (t["new_owner"].isna() | ~pd.Series([o in f for o, f in zip(t["new_owner"], t["filed_by"])],
                                                                  index=t.index, dtype=bool)))
    t["new_owner"] = t["new_owner"].where(t["new_owner"].notna() | ~t["is_real_change"], UNKNOWN_OWNER)
    t["from_individuals"] = t["appln_id"].map(
        application_applicants.groupby("appln_id")["applicant_type"].agg(lambda s: set(s) == {"individual"}).to_dict()).fillna(False).astype(bool)
    t["publn_date"] = to_date(col_or_empty(t, "event_publn_date", pd.NaT))
    keep = ["appln_id", "docdb_family_id", "event_auth", "event_code", "date", "publn_date", "event_descr", "category", "party_old",
            "party_new", "new_owner_name", "new_owner", "is_real_change", "from_individuals"]
    ownership_events = t[[c for c in keep if c in t.columns]].assign(
        filed_by=t["filed_by"].apply(lambda s: " | ".join(sorted(s)))).sort_values(["docdb_family_id", "appln_id", "date"])
    ownership_events.to_csv(C.OUT_STEP1 / "ownership_events.csv", index=False)

    real = ownership_events[ownership_events["is_real_change"]]
    # CN transfers can come as several rows, only one with the new owner: drop the unnamed duplicates of a named change
    named = real.loc[real["new_owner"].ne(UNKNOWN_OWNER), ["appln_id", "publn_date"]].drop_duplicates()
    dup = real.merge(named.assign(_named=True), on=["appln_id", "publn_date"], how="left")["_named"].fillna(False).astype(bool).to_numpy()
    real = real[~(dup & real["new_owner"].eq(UNKNOWN_OWNER).to_numpy(dtype=bool))]
    last_real = real.sort_values("date").groupby("appln_id")
    last_date = last_real["date"].max()
    # current owners: all the new owners recorded on the date of the latest real change
    latest = real.merge(last_date.rename("last_date"), on="appln_id")
    latest = latest[latest["date"].eq(latest["last_date"]) | latest["last_date"].isna()]
    current_after_change = latest.groupby("appln_id")["new_owner"].agg(lambda s: " | ".join(sorted(set(s.dropna()))))
    cur = application_applicants.groupby(["appln_id", "docdb_family_id"])["applicant"].agg(
        lambda s: " | ".join(sorted(set(s.dropna())))).rename("owners_at_filing").reset_index()
    cur["n_owner_changes"] = cur["appln_id"].map(real.groupby("appln_id").size().to_dict()).fillna(0).astype(int)
    cur["owner_changed"] = cur["n_owner_changes"] > 0
    cur["first_owner_change_date"] = pd.to_datetime(cur["appln_id"].map(real.groupby("appln_id")["date"].min().to_dict()))
    cur["last_owner_change_date"] = pd.to_datetime(cur["appln_id"].map(last_date.to_dict()))
    cur["current_owners"] = cur["appln_id"].map(current_after_change.to_dict()).fillna(cur["owners_at_filing"])
    cur["has_security_interest"] = cur["appln_id"].isin(ownership_events.loc[ownership_events["category"].eq("security"), "appln_id"])
    cur["has_licence_recorded"] = cur["appln_id"].isin(ownership_events.loc[ownership_events["category"].eq("licence"), "appln_id"])
    cur.to_csv(C.OUT_STEP1 / "current_owners.csv", index=False)

    fam_last = real.groupby("docdb_family_id")["date"].max()
    fam_latest = real.merge(fam_last.rename("fam_last"), on="docdb_family_id")
    fam_latest = fam_latest[fam_latest["date"].eq(fam_latest["fam_last"]) | fam_latest["fam_last"].isna()]
    changed_owner = fam_latest.groupby("docdb_family_id")["new_owner"].agg(lambda s: sorted(set(s.dropna())))
    filing_owner = applicants.groupby("docdb_family_id")["applicant"].agg(lambda s: sorted(set(s)))
    fam_cur = filing_owner.to_frame("owners")
    fam_cur.loc[changed_owner.index.intersection(fam_cur.index), "owners"] = changed_owner
    family_current_owners = fam_cur.explode("owners").rename(columns={"owners": "current_owner"}).reset_index()
    family_current_owners["weight"] = 1 / family_current_owners.groupby("docdb_family_id")["current_owner"].transform("size")
    family_current_owners["owner_changed"] = family_current_owners["docdb_family_id"].isin(changed_owner.index)
    family_current_owners.to_csv(C.OUT_STEP1 / "family_current_owners.csv", index=False)

    fam_own = pd.DataFrame({
        "owner_changed": cur.groupby("docdb_family_id")["owner_changed"].any(),
        "n_owner_changes": real.groupby("docdb_family_id").size(),
        "first_owner_change_date": real.groupby("docdb_family_id")["date"].min(),
        "last_owner_change_date": fam_last,
        "owner_change_from_individuals": real.groupby("docdb_family_id")["from_individuals"].any(),
        "has_security_interest": cur.groupby("docdb_family_id")["has_security_interest"].any(),
    })
    fam_own["current_owners"] = fam_cur["owners"].apply(lambda v: " | ".join(v) if isinstance(v, list) else v)
    fam_own.index.name = "docdb_family_id"
    families = families.drop(columns=[c for c in fam_own.columns if c in families.columns])
    families = families.merge(fam_own.reset_index(), on="docdb_family_id", how="left")
    for c in ["owner_changed", "owner_change_from_individuals", "has_security_interest"]:
        families[c] = families[c].fillna(False).astype(bool)
    families["n_owner_changes"] = families["n_owner_changes"].fillna(0).astype(int)

    print(f"transfer-type events: {len(t):,} | real changes of owner: {int(t['is_real_change'].sum()):,} "
          f"| families whose owner changed: {families['owner_changed'].mean():.1%} "
          f"| families pledged as collateral: {families['has_security_interest'].mean():.1%}")
    return families


def benchmarks(patstat, db, families, applications):
    if not C.FETCH_BENCHMARK:
        return

    def families_per_year(cpc_prefixes=None):
        year = func.min(TLS201_APPLN.earliest_filing_year)
        q = db.query(TLS201_APPLN.docdb_family_id.label("fid"), year.label("year")).filter(
            TLS201_APPLN.ipr_type == C.IPR_TYPE, TLS201_APPLN.earliest_filing_year != C.MISSING_YEAR)
        if cpc_prefixes:
            q = q.join(TLS224_APPLN_CPC, TLS224_APPLN_CPC.appln_id == TLS201_APPLN.appln_id).filter(
                func.replace(TLS224_APPLN_CPC.cpc_class_symbol, " ", "").like(cpc_prefixes[0] + "%")
                if len(cpc_prefixes) == 1 else
                func.substr(func.replace(TLS224_APPLN_CPC.cpc_class_symbol, " ", ""), 1, 4).in_(list(cpc_prefixes)))
        sq = q.group_by(TLS201_APPLN.docdb_family_id).having(year.between(C.YEAR_MIN, C.YEAR_MAX)).subquery()
        return pd.DataFrame(patstat.df(db.query(sq.c.year, func.count().label("families")).group_by(sq.c.year)))

    neighbour = "neighbour " + "/".join(C.BENCHMARK_CPC)
    bench = families.groupby("family_year").size().rename(C.SECTOR_NAME).to_frame()
    bench = bench.join(families_per_year().set_index("year")["families"].rename("all patents"), how="outer")
    bench = bench.join(families_per_year(C.BENCHMARK_CPC).set_index("year")["families"].rename(neighbour),
                       how="outer").fillna(0).sort_index()
    bench["sector share of all (per mille)"] = bench[C.SECTOR_NAME] / bench["all patents"] * 1000
    bench["is_truncated"] = bench.index > C.LAST_COMPLETE_YEAR
    bench.index.name = "year"
    bench.to_csv(C.OUT_STEP1 / "benchmark_families_per_year.csv")

    def applications_per_year(cpc_prefixes=None):
        year = TLS201_APPLN.appln_filing_year
        q = db.query(year.label("year"), func.count(func.distinct(TLS201_APPLN.appln_id)).label("applications")).filter(
            TLS201_APPLN.ipr_type == C.IPR_TYPE, year.between(C.YEAR_MIN, C.YEAR_MAX),
            TLS201_APPLN.appln_id > 0, TLS201_APPLN.appln_id < C.INVALID_APPLN_ID)
        if cpc_prefixes:
            q = q.join(TLS224_APPLN_CPC, TLS224_APPLN_CPC.appln_id == TLS201_APPLN.appln_id).filter(
                func.replace(TLS224_APPLN_CPC.cpc_class_symbol, " ", "").like(cpc_prefixes[0] + "%"))
        return pd.DataFrame(patstat.df(q.group_by(year))).set_index("year")["applications"]

    in_window = applications[applications["in_appln_window"]]
    bench_app = pd.DataFrame({
        C.SECTOR_NAME: in_window.groupby("appln_filing_year")["appln_id"].nunique(),
        "all patents": applications_per_year(),
        neighbour: applications_per_year(C.BENCHMARK_CPC),
    }).fillna(0).sort_index()
    bench_app.index.name = "year"
    bench_app["is_truncated"] = bench_app.index > C.LAST_COMPLETE_YEAR
    bench_app.to_csv(C.OUT_STEP1 / "benchmark_applications_per_year.csv")


def quality_checks(families, applications, applicants, family_subclass, application_subclass, family_ids):
    checks = {
        "families: one row per family": families["docdb_family_id"].is_unique,
        "family year inside the window": families["family_year"].between(C.YEAR_MIN, C.YEAR_MAX).all(),
        "applicants: one row per family x group": not applicants.duplicated(["docdb_family_id", "applicant"]).any(),
        "applicant weights sum to 1 per family": np.allclose(applicants.groupby("docdb_family_id")["weight"].sum(), 1),
        "applicant country weights sum to 1 (families with a known country)":
            np.allclose(applicants.groupby("docdb_family_id")["country_weight"].sum(min_count=1).dropna(), 1),
        "subclass weights sum to 1 per family": np.allclose(family_subclass.groupby("docdb_family_id")["weight"].sum(), 1),
        "subclass weights sum to 1 per application": np.allclose(application_subclass.groupby("appln_id")["weight"].sum(), 1),
        "grant lag never negative": not (families.get("grant_lag", pd.Series(dtype=float)) < 0).any(),
        "grant date never before filing date": not (applications["grant_date"] < applications["appln_filing_date"]).any(),
        "priority date never after filing date": not (applications["priority_date"] > applications["appln_filing_date"]).any(),
        "first publication never before priority": not (applications["first_publn_date"] < applications["priority_date"]).any(),
        "family status defined for every family": families["family_status"].notna().all(),
    }
    failed = [k for k, v in checks.items() if not v]
    print(f"Quality checks: {len(checks) - len(failed)} of {len(checks)} passed" + (f" | FAILED: {'; '.join(failed)}" if failed else ""))
    no_country = ~families["docdb_family_id"].isin(applicants.loc[applicants["country_weight"].notna(), "docdb_family_id"])
    print(f"Without English abstract: {families['abstract_en'].isna().sum():,} | without applicant: "
          f"{len(set(family_ids) - set(applicants['docdb_family_id'])):,} | applicant UNKNOWN: "
          f"{(applicants['applicant'] == 'UNKNOWN').sum():,} | without applicant country: {no_country.sum():,}")


def main():
    C.OUT_STEP1.mkdir(parents=True, exist_ok=True)
    patstat = PatstatClient(env=C.ENV)
    db = patstat.orm()

    # tables that some tipdata versions name differently come back as None
    tls = {name: get_model(model) for name, model in [
        ("tls211", "TLS211_PAT_PUBLN"), ("tls212", "TLS212_CITATION"), ("tls215", "TLS215_CITN_CATEG"),
        ("tls228", "TLS228_DOCDB_FAM_CITN"), ("tls230", "TLS230_APPLN_TECHN_FIELD"),
        ("tls231", "TLS231_INPADOC_LEGAL_EVENT"), ("tls803", "TLS803_LEGAL_EVENT_CODE"),
        ("tls901", "TLS901_TECHN_FIELD_IPC")]}
    print({n: m is not None for n, m in tls.items()})

    sector, apps, titles, abstracts, cpc, family_ids, appln_ids = load_step0_outputs()

    families, applications = build_families(sector, apps, titles, abstracts)
    families, family_cpc, family_subclass, application_subclass = cpc_tables(cpc, families)
    wipo_fields(patstat, db, tls, appln_ids)
    publications, families = publication_tables(patstat, db, tls, apps, appln_ids, families)
    fwd, bwd, families = family_citations(patstat, db, tls, families, family_ids)
    citing_cpc(patstat, db, fwd, bwd)

    appl_raw = persons(patstat, db, TLS207_PERS_APPLN.applt_seq_nr > 0, appln_ids)
    print(f"applicant records: {len(appl_raw):,} | families with an applicant: "
          f"{appl_raw['docdb_family_id'].nunique():,} of {len(family_ids):,}")
    appl, anchor_map = harmonise_applicants(appl_raw)
    applicants, applicant_countries_by_application, application_applicants, families = applicant_tables(
        appl, families, appln_ids)
    _, families = inventor_tables(patstat, db, appln_ids, applicants, families)

    events, families = legal_event_tables(patstat, db, tls, apps, appln_ids, families)
    applications, families = life_cycle(patstat, db, apps, appln_ids, publications, events, applications, families)
    families = ownership_tables(events, anchor_map, applicants, application_applicants, families)
    benchmarks(patstat, db, families, applications)

    quality_checks(families, applications, applicants, family_subclass, application_subclass, family_ids)

    out = C.OUT_STEP1
    families.to_csv(out / "families.csv", index=False)
    family_cpc.to_csv(out / "family_cpc.csv", index=False)
    family_subclass.to_csv(out / "family_subclass.csv", index=False)
    application_subclass.to_csv(out / "application_subclass.csv", index=False)
    applications.drop(columns=["granted"]).to_csv(out / "applications.csv", index=False)
    applicant_countries_by_application.to_csv(out / "applicant_countries_by_application.csv", index=False)
    applicants.to_csv(out / "applicants.csv", index=False)
    application_applicants.to_csv(out / "application_applicants.csv", index=False)
    fwd.to_csv(out / "citations_forward.csv", index=False)
    bwd.to_csv(out / "citations_backward.csv", index=False)
    print(f"\nSaved in {out.resolve()}")


if __name__ == "__main__":
    main()
