"""Step 0 · clean datasets: families, applicants, dates, citations, legal events

Exported from notebooks/02_clean.ipynb by tools/export_py.py: do not edit by hand, edit the notebook and export again.
Run from anywhere inside the repository:  python src/02_clean.py
"""
# %%
try:                                   # inside Jupyter / IPython
    from IPython.display import display
except ImportError:                    # plain Python
    def display(*objs):
        for o in objs:
            print(o.to_string() if hasattr(o, "to_string") else o)

# %% [markdown]
# # Step 0 · Cleaning and family-level datasets
#
# Input: the sector built by `01_extract.ipynb` (`outputs/<env>/step0/`).
# Output: the clean datasets used from Step 1 on (`outputs/<env>/step1/`).
#
# The PW kick-off asks for: DOCDB family as unit, earliest priority year, harmonised applicants (PSN/HAN plus own
# rules for subsidiaries), truncation flag, and the tables `tls201`, `tls224`, `tls206/207`, `tls212`, `tls202/203`.
# The lectures add other information worth collecting (*who, what, when, where, how* of IM_Theory_3): inventors,
# legal status and renewals, oppositions and transfers, science links (NPL citations), citation categories,
# claims, grant lag. Each extra block can be switched off in `config.py` (`FETCH_...`).
#
# | Section | Output (`outputs/<env>/step1/`) | Used for (step · concept) |
# |---|---|---|
# | 2 Families and applications | `families.csv`, `applications.csv` | Step 1 volumes at both levels · Step 3 success, globalization |
# | 3 CPC | `family_cpc.csv`, `family_subclass.csv`, `application_subclass.csv` | Step 1 technology mix (fractional) · Step 2 subfields · Step 4 novel recombination |
# | 4 WIPO fields | `family_wipo_fields.csv` | Step 1 technology mix (Schmoch, 2008) |
# | 5 Publications | columns in `families.csv` | grant lag, number of claims (Squicciarini et al., 2013) |
# | 7.4 Owners per application | `application_applicants.csv` | every name of every applicant of every application (person, PSN, HAN, harmonised group) |
# | 9.2 Ownership changes | `ownership_events.csv`, `current_owners.csv`, `family_current_owners.csv`, columns in `families.csv` | owner today vs applicant at filing, market for technology, collateral (IM_Theory_1–3) |
# | 9.1 Life cycle | dates and status in `applications.csv` and `families.csv`, `publications.csv` | every date from priority to expiry, status today, residual life (IM_Theory_1, IM_Theory_3) |
# | 6 Citations | `citations_forward.csv`, `citations_backward.csv`, `citation_pairs.csv` (family pairs with who added the citation: applicant or office), `citation_categories.csv`, `cited_citing_cpc.csv` | Step 3 influence · Step 4 originality, generality · science linkage (NPL) |
# | 7 Applicants | `applicants.csv`, `applicant_countries_by_application.csv`, `applicants_to_review.csv`, `applicants_audit.csv`, `applicants_profile.csv` | Step 1 geography · Step 2 HHI, entry, top players · Step 6 co-assignments |
# | 8 Inventors | `inventors.csv` (with the NUTS region of European inventors), `inventor_countries_by_application.csv` | Step 1 geography of R&D · team size, collaboration networks |
# | 9 Legal events | `legal_events.csv` + flags in `families.csv` | active families, renewals, oppositions, transfers (IM_Theory_1–2) |
# | 10 Benchmarks | `benchmark_families_per_year.csv`, `benchmark_applications_per_year.csv` | Step 1 growth vs all patents and vs a neighbouring field |
#
# **Counting rules** (Tutorial 0, Notebook 2, section 4; state them in the technical appendix):
# - **Unit**: the DOCDB family (PW rule); every family-level table also has an application-level twin, so Step 1
#   can show both levels side by side as in Tutorial 0.
# - **Truncation**: years after `LAST_COMPLETE_YEAR` are flagged (`is_truncated`, `is_truncated_appln`); figures shade
#   them, and growth rates must never use them.
# - **PCT**: `appln_auth = 'WO'` and `appln_kind` starting with `W`. Family level: at least one PCT application.
# - **Fractional counting** (`pw_utils.fractional_weights`): each unit is split equally among its distinct persons
#   with a known country (countries), or among its distinct CPC subclasses (technology). Every unit sums to 1.
#
# Family year = earliest priority of the whole family; language is not a filter (only `has_en_abstract` for NLP);
# applicants are consolidated into corporate groups with documented rules.

# %% [markdown]
# ## 1 · Setup

# %%
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import and_, func

from epo.tipdata.patstat import PatstatClient
from epo.tipdata.patstat.database.models import (
    TLS201_APPLN,
    TLS206_PERSON,
    TLS207_PERS_APPLN,
    TLS224_APPLN_CPC,
)

# the repository's src/ folder (config.py, pw_utils.py): works from notebooks/, src/ or the repository root
_here = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd().resolve()   # script or notebook
_src = next((d / "src" for d in (_here, *_here.parents) if (d / "src" / "config.py").exists()), _here)
if str(_src) not in sys.path:
    sys.path.append(str(_src))
import config as C
from pw_utils import (applicant_type, available_columns, clean_country, clean_patent_text, fetch_by_ids,
                      fractional_weights, get_model, legal_event_flags, normalise_name, to_group)

RAW = C.OUT_STEP0 / "raw"
C.OUT_STEP1.mkdir(parents=True, exist_ok=True)

patstat = PatstatClient(env=C.ENV)
db = patstat.orm()

# optional tables: None if this tipdata version names them differently
TLS211 = get_model("TLS211_PAT_PUBLN")
TLS212 = get_model("TLS212_CITATION")
TLS215 = get_model("TLS215_CITN_CATEG")
TLS228 = get_model("TLS228_DOCDB_FAM_CITN")          # table tls228_docdb_fam_citn (Tutorial 0, schema list)
TLS230 = get_model("TLS230_APPLN_TECHN_FIELD")
TLS231 = get_model("TLS231_INPADOC_LEGAL_EVENT")
TLS803 = get_model("TLS803_LEGAL_EVENT_CODE")
TLS901 = get_model("TLS901_TECHN_FIELD_IPC")
print({n: m is not None for n, m in [("tls211", TLS211), ("tls212", TLS212), ("tls215", TLS215), ("tls228", TLS228),
                                      ("tls230", TLS230), ("tls231", TLS231), ("tls803", TLS803), ("tls901", TLS901)]})


def fetch(make_query, ids, columns=None):
    """Chunked query; returns an empty frame with `columns` when nothing comes back."""
    out = fetch_by_ids(patstat, make_query, ids)
    return pd.DataFrame(columns=columns) if out.empty and columns else out

# %%
sector = pd.read_csv(C.OUT_STEP0 / "sector_families.csv")
apps = pd.read_csv(RAW / "applications.csv.gz")
titles = pd.read_csv(RAW / "titles.csv.gz")
abstracts = pd.read_csv(RAW / "abstracts.csv.gz")
cpc = pd.read_csv(RAW / "cpc.csv.gz")

assert sector["docdb_family_id"].is_unique, "sector_families.csv must have one row per family"
family_ids = sector["docdb_family_id"].astype("int64").tolist()
appln_ids = apps["appln_id"].astype("int64").unique().tolist()
print(f"env {C.ENV} | IPR {C.IPR_TYPE} | sector families: {len(family_ids):,} | applications: {len(appln_ids):,}")

# %% [markdown]
# ## 2 · Family table
#
# - `family_year`: earliest priority year of the family (minimum over its applications), computed in `01_extract`.
# - `is_truncated`: family year after `LAST_COMPLETE_YEAR`; shaded in figures and excluded from growth rates.
# - `granted_any`: at least one application of the family was granted (success; only on filings old enough).
# - `n_offices`, `n_national_offices`: distinct filing offices, with and without `WO` and `EP` (globalization).
# - `is_international`: protection sought beyond the first office (IM_Theory_3, "international family").
# - `via_pct`: at least one PCT application (`appln_auth = 'WO'` and `appln_kind` starting with `W`, Tutorial 0).
# - `inpadoc_family_id`: extended family (linked through at least one shared priority). The PW uses the DOCDB
#   simple family; the INPADOC id is kept to check that results do not change with the wider definition.
# - `title_en`, `abstract_en`, `clean_abstract`: English text for NLP only.

# %%
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

# application-level twin (Tutorial 0: the sector is selected at family level, then all its applications are counted)
# apps already carries family_year (computed in 01_extract); add only the family truncation flag
applications = apps.merge(families[["docdb_family_id", "is_truncated"]], on="docdb_family_id", how="left")
applications = applications.rename(columns={"is_truncated": "is_truncated_family"})
applications["is_truncated_appln"] = applications["appln_filing_year"] > C.LAST_COMPLETE_YEAR
applications["in_appln_window"] = applications["appln_filing_year"].between(C.YEAR_MIN, C.YEAR_MAX)
applications["is_granted"] = applications["granted"].eq("Y")
print(f"applications: {len(applications):,} | per family: mean {len(applications) / len(families):.2f} "
      f"| outside {C.YEAR_MIN}-{C.YEAR_MAX} by filing year: {(~applications['in_appln_window']).sum():,} "
      f"(kept; filter with in_appln_window for application-level counts, as in Tutorial 0)")
print(f"families: {len(families):,} | English abstract: {families['abstract_en'].notna().sum():,} "
      f"| granted: {families['granted_any'].mean():.1%} | via PCT: {families['via_pct'].mean():.1%} "
      f"| international: {families['is_international'].mean():.1%} | truncated: {families['is_truncated'].sum():,}")

# %% [markdown]
# ## 3 · CPC codes per family
#
# One row per family × CPC symbol, with the **subclass** (4 characters, e.g. `B63G`) and the **main group**
# (e.g. `B63G8/`). Section `Y` (cross-sectional tagging, e.g. `Y02E`) is flagged: it describes an application
# domain, not a technology. Fractional counts (1/k per subclass, Tutorial 0) are computed in Step 1.

# %%
family_cpc = cpc[["docdb_family_id", "cpc"]].drop_duplicates().copy()
family_cpc["subclass"] = family_cpc["cpc"].str[:4]
family_cpc["main_group"] = family_cpc["cpc"].str.extract(r"^([A-Z]\d{2}[A-Z]\d+/)", expand=False)
family_cpc["is_tagging"] = family_cpc["cpc"].str.startswith("Y")
families = families.merge(
    family_cpc[~family_cpc["is_tagging"]].groupby("docdb_family_id")["subclass"].nunique().rename("n_subclasses"),
    on="docdb_family_id", how="left")
# fractional counts by subclass: `weight` uses all subclasses (Tutorial 0), `weight_tech` leaves out the Y tagging section
family_subclass = fractional_weights(family_cpc, "docdb_family_id", "subclass")[["docdb_family_id", "subclass", "weight"]]
family_subclass = family_subclass.merge(
    fractional_weights(family_cpc[~family_cpc["is_tagging"]], "docdb_family_id", "subclass")[
        ["docdb_family_id", "subclass", "weight"]].rename(columns={"weight": "weight_tech"}),
    on=["docdb_family_id", "subclass"], how="left")
app_cpc = cpc.assign(subclass=cpc["cpc"].str[:4])
application_subclass = fractional_weights(app_cpc, "appln_id", "subclass")[["appln_id", "docdb_family_id", "subclass", "weight"]]
print(f"family_cpc: {len(family_cpc):,} rows | distinct subclasses: {family_cpc['subclass'].nunique():,}")
display(family_cpc.drop_duplicates(["docdb_family_id", "subclass"])["subclass"].value_counts().head(15)
        .to_frame("families"))

# %% [markdown]
# ## 4 · WIPO technology fields
#
# 35 fields in 5 sectors (Schmoch, 2008), from `tls230` (weights summing to 1 per application) and `tls901`
# (names). Weights are rescaled so that every **family** sums to 1, as in Tutorial 0, Notebook 2.

# %%
if C.FETCH_WIPO_FIELDS and TLS230 is not None:
    wf = fetch(lambda ch: db.query(TLS201_APPLN.docdb_family_id, TLS230.appln_id, TLS230.techn_field_nr, TLS230.weight)
               .join(TLS230, TLS230.appln_id == TLS201_APPLN.appln_id)
               .filter(TLS201_APPLN.appln_id.in_(ch)), appln_ids)
    if not wf.empty:
        wf[["appln_id", "techn_field_nr", "weight"]].to_csv(C.OUT_STEP1 / "application_wipo_fields.csv", index=False)  # sums to 1 per application
        wf = wf.groupby(["docdb_family_id", "techn_field_nr"], as_index=False)["weight"].sum()
        wf["weight"] = wf["weight"] / wf.groupby("docdb_family_id")["weight"].transform("sum")
        if TLS901 is not None:
            names = pd.DataFrame(patstat.df(db.query(TLS901.techn_field_nr, TLS901.techn_field,
                                                     TLS901.techn_sector).distinct())).drop_duplicates("techn_field_nr")
            wf = wf.merge(names, on="techn_field_nr", how="left")
        wf.to_csv(C.OUT_STEP1 / "family_wipo_fields.csv", index=False)
        if "techn_sector" in wf:
            display(wf.groupby("techn_sector")["weight"].sum().div(wf["weight"].sum()).mul(100).round(1)
                    .sort_values(ascending=False).to_frame("% of families"))

# %% [markdown]
# ## 5 · Publications: grant date and claims
#
# From `tls211_pat_publn`: the first grant publication of each family gives the **grant lag** (years from the
# priority to the first grant: examiners take 2–5 years, IM_Theory_1), and `publn_claims` gives the **number of
# claims**, a quality indicator of the OECD set (Squicciarini, Dernis & Criscuolo, 2013): more claims, broader
# protection. The publication ids are also needed for the citation details of Section 6.
# All the other dates of the life of each patent (publication, search report, grant, opposition window, renewals,
# end of the right, expiry, status today) are built in Section 9.1.

# %%
publications = pd.DataFrame()
if C.FETCH_PUBLICATIONS and TLS211 is not None:
    cols = available_columns(TLS211, ["pat_publn_id", "appln_id", "publn_auth", "publn_kind", "publn_date",
                                      "publn_first_grant", "publn_claims"])
    publications = fetch(lambda ch: db.query(*cols).filter(TLS211.appln_id.in_(ch)), appln_ids)
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
    if "publn_claims" in publications:
        claims = (pd.to_numeric(publications["publn_claims"], errors="coerce").replace(0, np.nan)
          .groupby(publications["docdb_family_id"]).max().rename("max_claims").reset_index())
        families = families.merge(claims, on="docdb_family_id", how="left")
        print(f"median number of claims: {families['max_claims'].median()}")
    print(f"publications: {len(publications):,}")

# %% [markdown]
# ## 6 · Citations
#
# ### 6.1 Family-level citation links (`tls228_docdb_fam_citn`)
# A citing DOCDB family → a cited DOCDB family, so each invention is counted once even when several publications
# of the same family cite the same document.
#
# - **forward** citations: families citing a sector family (influence, Hall, Jaffe & Trajtenberg 2001);
#   `within_5y` allows a fixed 5-year window, `fwd_window_complete` marks families whose window is fully observed;
# - **backward** citations: families cited by a sector family (originality, Trajtenberg et al. 1997);
# - `citing_in_sector`: knowledge flows inside the sector (spillovers, IM_Theory_4).

# %%
def family_years(ids):
    if not len(ids):
        return pd.DataFrame(columns=["docdb_family_id", "family_year"])
    return fetch(
        lambda ch: db.query(TLS201_APPLN.docdb_family_id,
                            func.min(TLS201_APPLN.earliest_filing_year).label("family_year"))
        .filter(TLS201_APPLN.docdb_family_id.in_(ch))
        .filter(TLS201_APPLN.earliest_filing_year != C.MISSING_YEAR)
        .group_by(TLS201_APPLN.docdb_family_id),
        ids,
    )


if TLS228 is None:
    print("tls228_docdb_fam_citn not available: family citations skipped (Section 6.2 still works at publication level)")
    fwd = pd.DataFrame(columns=["citing_family_id", "docdb_family_id"])
    bwd = pd.DataFrame(columns=["docdb_family_id", "cited_family_id"])
else:
    cols228 = ["docdb_family_id", "cited_docdb_family_id"]
    fwd = fetch(lambda ch: db.query(TLS228.docdb_family_id, TLS228.cited_docdb_family_id)
                .filter(TLS228.cited_docdb_family_id.in_(ch)), family_ids, cols228)
    bwd = fetch(lambda ch: db.query(TLS228.docdb_family_id, TLS228.cited_docdb_family_id)
                .filter(TLS228.docdb_family_id.in_(ch)), family_ids, cols228)
    fwd = fwd.rename(columns={"docdb_family_id": "citing_family_id", "cited_docdb_family_id": "docdb_family_id"})
    bwd = bwd.rename(columns={"cited_docdb_family_id": "cited_family_id"})

fwd = fwd[fwd["citing_family_id"] != fwd["docdb_family_id"]].drop_duplicates()
bwd = bwd[bwd["cited_family_id"] != bwd["docdb_family_id"]].drop_duplicates()

citing_years = family_years(fwd["citing_family_id"].unique()).rename(
    columns={"docdb_family_id": "citing_family_id", "family_year": "citing_year"})
fwd = (fwd.merge(citing_years, on="citing_family_id", how="left")
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

# %% [markdown]
# ### 6.2 Citation origin, X/Y/A categories and science links (`tls212`, `tls215`)
#
# At publication level PATSTAT also tells **who** added a citation (`citn_origin`: applicant, search report,
# examination...) and the **search-report category** (`tls215`, IM_Theory_1):
# **X** = relevant alone (novelty or inventive step at stake), **Y** = relevant combined with other documents,
# **A** = technological background.
#
# Per family we compute:
# - `bwd_npl_cits`: citations to non-patent literature (mostly scientific articles): **science linkage**;
# - `bwd_X_share`: share of backward citations in category X (the invention's novelty was challenged);
# - `fwd_X_cits`: how many times later publications cited this family as **X**, i.e. as *blocking prior art*:
#   a sign of an important, blocking patent (useful for the future score and for freedom-to-operate risks, Step 6).
#
# **Counting unit.** TLS212 links *publications*: the A1, the B1 and the WO publication of the same invention can
# each cite (or be cited by) the same document. To count each invention once (IM_Theory_1: *"link them through the
# application number, never count both"*), both ends of every link are mapped to their DOCDB family before counting:
# the counts below are **distinct (citing family, cited family)** pairs, and a pair is X if at least one of its
# publication links is X. NPL documents keep their own id; artificial publications (no family) keep the publication id.

# %%
if C.FETCH_CITATION_DETAIL and TLS212 is not None and not publications.empty:
    pub_ids = publications["pat_publn_id"].astype("int64").unique().tolist()
    pub2fam = publications[["pat_publn_id", "docdb_family_id"]].drop_duplicates()
    c212 = available_columns(TLS212, ["pat_publn_id", "citn_replenished", "citn_id", "citn_origin",
                                      "cited_pat_publn_id", "cited_appln_id", "cited_npl_publn_id"])
    bwd_pub = fetch(lambda ch: db.query(*c212).filter(TLS212.pat_publn_id.in_(ch)), pub_ids)
    fwd_pub = fetch(lambda ch: db.query(*c212).filter(TLS212.cited_pat_publn_id.in_(ch)), pub_ids)

    def add_categories(df):
        if TLS215 is None or df.empty:
            return df.assign(citn_categ=pd.NA)
        cat = fetch(lambda ch: db.query(TLS215.pat_publn_id, TLS215.citn_replenished, TLS215.citn_id,
                                        TLS215.citn_categ).filter(TLS215.pat_publn_id.in_(ch)),
                    df["pat_publn_id"].astype("int64").unique().tolist())
        if cat.empty:
            return df.assign(citn_categ=pd.NA)
        cat["citn_categ"] = cat["citn_categ"].astype(str).str.strip()
        cat = cat.groupby(["pat_publn_id", "citn_replenished", "citn_id"])["citn_categ"].agg(
            lambda s: "".join(sorted(set("".join(s))))).reset_index()
        return df.merge(cat, on=["pat_publn_id", "citn_replenished", "citn_id"], how="left")

    bwd_pub = add_categories(bwd_pub).merge(pub2fam, on="pat_publn_id", how="left")
    fwd_pub = add_categories(fwd_pub).merge(
        pub2fam.rename(columns={"pat_publn_id": "cited_pat_publn_id"}), on="cited_pat_publn_id", how="left")
    # NPL citation = a citation to non-patent literature. PATSTAT stores the NPL id as text in some editions, and
    # the cited patent ids are 0 on NPL rows: both signals are used, so the flag does not depend on the column type.
    def nonzero(col):
        if col not in bwd_pub:
            return pd.Series(False, index=bwd_pub.index)
        v = bwd_pub[col].astype("string").str.strip()
        return v.notna() & ~v.isin(["", "0", "0.0", "None", "nan", "<NA>"])
    has_npl_id = nonzero("cited_npl_publn_id")
    no_patent = (~nonzero("cited_pat_publn_id") & ~nonzero("cited_appln_id")
                 if {"cited_pat_publn_id", "cited_appln_id"} <= set(bwd_pub) else pd.Series(False, index=bwd_pub.index))
    bwd_pub["is_npl"] = has_npl_id | no_patent
    print(f"backward citation rows: {len(bwd_pub):,} | with an NPL id: {int(has_npl_id.sum()):,} "
          f"| with no cited patent: {int(no_patent.sum()):,} (columns read: {', '.join(map(str, bwd_pub.columns))})")
    for d in (bwd_pub, fwd_pub):
        d["is_X"] = d["citn_categ"].fillna("").str.contains("X")

    # One count per (citing family, cited family): the A1, B1, WO... publications of the same invention count once
    # (IM_Theory_1: "link them through the application number, never count both"). TLS212 links publications, so
    # both ends are mapped to their DOCDB family before counting; NPL documents keep their own id.
    def pub_to_family(ids, name):
        ids = pd.Series(ids).dropna().astype("int64").unique().tolist()
        if not ids:
            return pd.DataFrame(columns=["pat_publn_id", name])
        return fetch(lambda ch: db.query(TLS211.pat_publn_id.label("pat_publn_id"),
                                         TLS201_APPLN.docdb_family_id.label(name))
                     .join(TLS201_APPLN, TLS201_APPLN.appln_id == TLS211.appln_id)
                     .filter(TLS211.pat_publn_id.in_(ch)), ids)

    # backward: cited patent publication -> cited family
    cited_fam = pub_to_family(bwd_pub.loc[~bwd_pub["is_npl"], "cited_pat_publn_id"], "cited_family_id")
    bwd_pub = bwd_pub.merge(cited_fam.rename(columns={"pat_publn_id": "cited_pat_publn_id"}).drop_duplicates("cited_pat_publn_id"),
                            on="cited_pat_publn_id", how="left")
    bwd_pub["cited_key"] = np.where(
        bwd_pub["is_npl"], "npl:" + bwd_pub["cited_npl_publn_id"].astype(str),
        np.where(bwd_pub["cited_family_id"].notna(),
                 "fam:" + pd.to_numeric(bwd_pub["cited_family_id"], errors="coerce").astype("Int64").astype(str),
                 "pub:" + bwd_pub["cited_pat_publn_id"].astype(str)))           # artificial publications: no family
    # who added the citation (TLS212 citn_origin): APP = the applicant; SEA / ISR / SUP / EXA / OPP... = the office
    # (search, examination, opposition). A family pair can have both: it keeps every origin seen on its publications.
    if "citn_origin" in bwd_pub.columns:
        bwd_pub["citn_origin"] = bwd_pub["citn_origin"].astype("string").str.strip().str.upper()
    else:
        bwd_pub["citn_origin"] = pd.NA
    bwd_pub["by_applicant"] = bwd_pub["citn_origin"].eq("APP").fillna(False)
    bwd_pub["by_office"] = bwd_pub["citn_origin"].notna() & ~bwd_pub["citn_origin"].eq("APP").fillna(False)
    bwd_pairs = (bwd_pub[~bwd_pub["cited_key"].eq("fam:" + bwd_pub["docdb_family_id"].astype(str))]   # no self-family
                 .groupby(["docdb_family_id", "cited_key"]).agg(
                     is_npl=("is_npl", "first"), is_X=("is_X", "any"), by_applicant=("by_applicant", "any"),
                     by_office=("by_office", "any"),
                     origins=("citn_origin", lambda s: " ".join(sorted(set(s.dropna().astype(str))))))
                 .reset_index())
    citation_pairs = bwd_pairs[bwd_pairs["cited_key"].str.startswith("fam:")].assign(
        cited_family_id=lambda d: d["cited_key"].str[4:].astype("int64"))[
        ["docdb_family_id", "cited_family_id", "is_X", "by_applicant", "by_office", "origins"]]
    citation_pairs.to_csv(C.OUT_STEP1 / "citation_pairs.csv", index=False)
    print("citation pairs by origin:", {"applicant only": int((citation_pairs.by_applicant & ~citation_pairs.by_office).sum()),
                                        "office only": int((~citation_pairs.by_applicant & citation_pairs.by_office).sum()),
                                        "both": int((citation_pairs.by_applicant & citation_pairs.by_office).sum())})

    # forward: citing publication -> citing family
    citing_fam = pub_to_family(fwd_pub["pat_publn_id"], "citing_family_id")
    fwd_pub = fwd_pub.merge(citing_fam.drop_duplicates("pat_publn_id"), on="pat_publn_id", how="left")
    fwd_pub["citing_family_id"] = pd.to_numeric(fwd_pub["citing_family_id"], errors="coerce").fillna(
        -pd.to_numeric(fwd_pub["pat_publn_id"], errors="coerce"))                # unknown family: the publication alone
    fwd_pairs = (fwd_pub[fwd_pub["citing_family_id"] != fwd_pub["docdb_family_id"]]
                 .groupby(["citing_family_id", "docdb_family_id"])["is_X"].any().reset_index())
    print(f"backward: {len(bwd_pub):,} publication links -> {len(bwd_pairs):,} family pairs | "
          f"forward: {len(fwd_pub):,} publication links -> {len(fwd_pairs):,} family pairs")

    detail = pd.DataFrame({
        "bwd_npl_cits": bwd_pairs[bwd_pairs["is_npl"]].groupby("docdb_family_id").size(),
        "bwd_pat_cits_pub": bwd_pairs[~bwd_pairs["is_npl"]].groupby("docdb_family_id").size(),   # distinct cited families
        "bwd_X_cits": bwd_pairs[bwd_pairs["is_X"] & ~bwd_pairs["is_npl"]].groupby("docdb_family_id").size(),
        "fwd_X_cits": fwd_pairs[fwd_pairs["is_X"]].groupby("docdb_family_id").size(),            # distinct citing families with an X
    }).fillna(0).astype(int)
    detail.index.name = "docdb_family_id"
    detail["bwd_X_share"] = detail["bwd_X_cits"] / detail["bwd_pat_cits_pub"].replace(0, np.nan)
    families = families.merge(detail.reset_index(), on="docdb_family_id", how="left")
    families[["bwd_npl_cits", "bwd_X_cits", "fwd_X_cits"]] = families[["bwd_npl_cits", "bwd_X_cits", "fwd_X_cits"]].fillna(0)

    keep = ["docdb_family_id", "pat_publn_id", "cited_pat_publn_id", "cited_npl_publn_id", "citn_origin", "citn_categ"]
    pd.concat([bwd_pub.assign(direction="backward"), fwd_pub.assign(direction="forward")])[
        [c for c in keep + ["direction"] if c in bwd_pub.columns or c == "direction"]
    ].to_csv(C.OUT_STEP1 / "citation_categories.csv", index=False)
    print(f"families with NPL citations: {(families['bwd_npl_cits'] > 0).mean():.1%} "
          f"| cited as X at least once: {(families['fwd_X_cits'] > 0).mean():.1%}")
    if "citn_origin" in bwd_pub:
        display(bwd_pub["citn_origin"].value_counts().to_frame("backward citations by origin"))

# %% [markdown]
# ### 6.3 CPC subclasses of cited and citing families
# Needed for originality (1 − Σ s² over the subclasses of cited patents) and generality (same over citing ones).

# %%
if C.FETCH_CITATION_CPC and (len(fwd) or len(bwd)):
    other_ids = pd.unique(pd.concat([fwd["citing_family_id"], bwd["cited_family_id"]]).astype("int64"))
    cit_cpc = fetch(
        lambda ch: db.query(TLS201_APPLN.docdb_family_id, TLS224_APPLN_CPC.cpc_class_symbol)
        .join(TLS224_APPLN_CPC, TLS201_APPLN.appln_id == TLS224_APPLN_CPC.appln_id)
        .filter(TLS201_APPLN.docdb_family_id.in_(ch))
        .distinct(),
        other_ids,
    )
    if not cit_cpc.empty:
        cit_cpc["subclass"] = cit_cpc["cpc_class_symbol"].str.replace(" ", "", regex=False).str[:4]
        cit_cpc = cit_cpc.loc[~cit_cpc["subclass"].str.startswith("Y"), ["docdb_family_id", "subclass"]].drop_duplicates()
        cit_cpc.to_csv(C.OUT_STEP1 / "cited_citing_cpc.csv", index=False)
        print(f"CPC subclasses of cited/citing families: {len(cit_cpc):,} rows")

# %% [markdown]
# ## 7 · Applicants
#
# ### 7.1 Extraction
# Applicants (`applt_seq_nr > 0`) of every application of the sector families, with PATSTAT's standardised
# names: `psn_name` (with `psn_sector`) and `han_name` (OECD HAN).

# %%
person_cols = [TLS201_APPLN.docdb_family_id, TLS201_APPLN.appln_id, TLS201_APPLN.appln_auth,
               TLS207_PERS_APPLN.applt_seq_nr, TLS207_PERS_APPLN.invt_seq_nr,
               TLS206_PERSON.person_id, TLS206_PERSON.person_name,
               TLS206_PERSON.psn_id, TLS206_PERSON.psn_name, TLS206_PERSON.psn_sector,
               TLS206_PERSON.han_id, TLS206_PERSON.han_name, TLS206_PERSON.person_ctry_code]
# NUTS region of the person's address (European persons only: Tutorial 3, nb 31), for the localisation test of Step 1/2
person_cols += [getattr(TLS206_PERSON, c) for c in ("nuts", "nuts_level") if hasattr(TLS206_PERSON, c)]


def persons(role_filter):
    return fetch(
        lambda ch: db.query(*person_cols)
        .select_from(TLS201_APPLN)
        .join(TLS207_PERS_APPLN, TLS201_APPLN.appln_id == TLS207_PERS_APPLN.appln_id)
        .join(TLS206_PERSON, TLS207_PERS_APPLN.person_id == TLS206_PERSON.person_id)
        .filter(TLS201_APPLN.appln_id.in_(ch))
        .filter(role_filter)
        .distinct(),
        appln_ids,
    )


appl_raw = persons(TLS207_PERS_APPLN.applt_seq_nr > 0)
print(f"applicant records: {len(appl_raw):,} | families with an applicant: "
      f"{appl_raw['docdb_family_id'].nunique():,} of {len(family_ids):,}")

# %% [markdown]
# ### 7.2 Harmonisation (the "name game", IM_Theory_1 and 3)
#
# Applicant identity and corporate-group identity are different things, so names are harmonised in **three
# separate layers**, each traceable in `applicants_audit.csv`:
#
# | Layer | Column | What it does | Can it merge two entities? |
# |---|---|---|---|
# | 1 · Normalisation | `name_norm` | one name at a time: accents, case, `&` → AND, punctuation, joined legal forms (`S P A` → SPA, `A/S` → AS), abbreviations (UNIV → UNIVERSITY), legal forms at the start and the end | no |
# | 2 · Anchor-prefix merge | `name_harmonised` | a name is absorbed into another applicant of the data whose **whole words** are its prefix (`ALPHA MARINE SERVICES` → `ALPHA`, but `ALPHAN SUBSEA` stays) | only spelling variants and branches with the same head name |
# | 3 · Corporate group | `applicant` | subsidiaries → owner, only from the verified list `config.GROUP_ALIASES` | yes, by documented ownership |
#
# Rules of layer 2 (data-driven, no hand-written company list): universities, individuals, `UNKNOWN` and names made
# only of generic words (`GENERIC_WORDS`) cannot be anchors; the longest anchor wins; applicants with at least
# `ESTABLISHED_MIN_FAMILIES` families are never absorbed (they are entities of their own); chains are followed.
#
# **Base name**: `psn_name`, else `han_name`, else `person_name` (Tutorial 0: PSN gives better results).
# **Type** (order of Tutorial 1): university / firm / government / hospital / individual; universities and public
# labs are typically non-practising entities (IM_Theory_2): partners or licensors rather than acquisition targets.
# **Country**: the person's country; if it is missing and `COUNTRY_FALLBACK_TO_OFFICE`, the national office of the
# application (EP and WO are not countries), recorded in `country_source` (`person`, `filing_office`, `unknown`).
# **Weights**: a family with *n* applicant groups gives 1/*n* to each (actor shares, HHI); country shares split each
# family among its groups with a known country (Tutorial 0). **Collaboration**: co-assigned families and
# firm–university co-assignments. **Client**: `is_client_group` marks the client's own patents.

# %%
import unicodedata

ESTABLISHED_MIN_FAMILIES = 5          # None switches the protection off (report the choice in the appendix)
COUNTRY_FALLBACK_TO_OFFICE = True
CLIENT_GROUP = "FINCANTIERI"
ABBREVIATIONS = {"UNIV": "UNIVERSITY", "INST": "INSTITUTE", "INTL": "INTERNATIONAL", "NATL": "NATIONAL",
                 "TECHNOL": "TECHNOLOGY"}
NON_ANCHOR_TYPES = {"university", "individual"}
# words too generic to identify an organisation; extend after reading applicants_audit.csv
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


def normalise_full(name):
    """Layer 1: accents, '&', joined legal forms and abbreviations, then pw_utils.normalise_name."""
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
    """Layer 2: map each normalised name to its harmonised name (whole-word anchor prefix, data-driven).
    name_table columns: name_norm, applicant_type, n_families."""
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
            for k in range(len(words) - 1, 0, -1):          # longest proper prefix first
                anchor = anchors.get(words[:k])
                if anchor is not None:
                    result = resolve(anchor)                 # follow the chain
                    break
        resolved[name] = result
        return result

    return {n: resolve(n) for n in info}


appl = appl_raw.copy()
for c in ["psn_name", "han_name", "person_name"]:
    appl[c] = appl[c].astype("string").str.strip().replace("", pd.NA)
appl["base_name"] = appl["psn_name"].fillna(appl["han_name"]).fillna(appl["person_name"]).fillna("")

# layer 1, once per distinct name
norm_map = {n: normalise_full(n) for n in appl["base_name"].unique()}
appl["name_norm"] = appl["base_name"].map(norm_map)
appl["type_row"] = [applicant_type(s, n) for s, n in zip(appl["psn_sector"], appl["name_norm"])]

# layer 2
name_table = (appl.groupby("name_norm")
              .agg(applicant_type=("type_row", lambda v: v[v != "unknown"].mode().iat[0] if (v != "unknown").any() else "unknown"),
                   n_families=("docdb_family_id", "nunique"))
              .reset_index())
anchor_map = build_anchor_map(name_table, ESTABLISHED_MIN_FAMILIES)
appl["name_harmonised"] = appl["name_norm"].map(anchor_map)
print(f"Layer 2 (anchor-prefix merge): {sum(k != v for k, v in anchor_map.items()):,} names absorbed into an anchor")

# layer 3
appl["applicant"] = appl["name_harmonised"].apply(to_group).replace("", "UNKNOWN")
appl["applicant_type"] = [applicant_type(s, n) for s, n in zip(appl["psn_sector"], appl["applicant"])]

# country, with the filing-office fallback
appl["country"] = appl["person_ctry_code"].apply(clean_country)
appl["country_source"] = np.where(appl["country"].notna(), "person", "unknown")
if COUNTRY_FALLBACK_TO_OFFICE:
    office = appl["appln_auth"].astype("string").str.strip()
    use_office = appl["country"].isna() & office.notna() & ~office.isin(["EP", "WO", ""])
    appl.loc[use_office, "country"] = office[use_office]
    appl.loc[use_office, "country_source"] = "filing_office"
print("Applicant country source (rows):", appl["country_source"].value_counts().to_dict())

# audit: source name -> layer 1 -> layer 2 -> layer 3, to review by hand
audit = (appl.groupby(["applicant", "name_harmonised", "name_norm", "base_name"])
         .agg(n_families=("docdb_family_id", "nunique")).reset_index()
         .sort_values(["n_families", "applicant"], ascending=[False, True]))
audit["merged_by_anchor"] = (audit["name_norm"] != audit["name_harmonised"]).astype(int)
audit["merged_by_group"] = (audit["name_harmonised"] != audit["applicant"]).astype(int)
audit.to_csv(C.OUT_STEP1 / "applicants_audit.csv", index=False)
print(f"Audit: {audit['base_name'].nunique():,} source names -> {audit['name_norm'].nunique():,} normalised -> "
      f"{audit['name_harmonised'].nunique():,} harmonised -> {audit['applicant'].nunique():,} groups")
display(audit[(audit["merged_by_anchor"] == 1) | (audit["merged_by_group"] == 1)].head(30))

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
applicants["weight"] = 1 / applicants.groupby("docdb_family_id")["applicant"].transform("size")   # actor shares, HHI
# country shares (Tutorial 0 rule): split each family among its distinct applicants with a KNOWN country
applicants = applicants.merge(
    fractional_weights(applicants, "docdb_family_id", "applicant", require="country")[
        ["docdb_family_id", "applicant", "weight"]].rename(columns={"weight": "country_weight"}),
    on=["docdb_family_id", "applicant"], how="left")
# application level: each application split among its distinct applicant persons with a known country
applicant_countries_by_application = fractional_weights(
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
display(applicants["applicant_type"].value_counts().to_frame("rows"))

# %% [markdown]
# ### 7.2.1 How good is the harmonisation? Evaluation on the Tutorial 1 gold standard
#
# Before trusting the applicant counts (concentration, top players, entrants), the method is measured on names whose
# right grouping is known: `labels/gold/PATSTAT_500_label_datav4.csv` (Tutorial 1, nb 11 §2.4: 3,192 PATSTAT names
# grouped by hand into 481 organisations, at corporate-group level). For each method, every name gets a predicted
# group; pairs of names are compared with the gold groups (pairwise **precision**: of the pairs merged, how many belong
# together, low = merging too much; **recall**: of the pairs that belong together, how many were merged, low = merging
# too little; **F1**), together with the number of groups found and the V-measure (homogeneity and completeness).
#
# Compared: no harmonisation, the two baselines of the tutorial (method A, legal forms only; the first word of method
# A) and our layers 1, 1+2 and 1+2+3. Layer 2 runs here without the protection of established applicants (the gold
# standard has no family counts), so it merges slightly more than on the sector data. The gold standard contains no
# underwater companies: layer 3 (`GROUP_ALIASES`, written for our sector) can only add a few merges.

# %%
from pw_utils import harmonisation_scores

GOLD_FILE = C.LABEL_DIR / "gold" / "PATSTAT_500_label_datav4.csv"
if GOLD_FILE.exists():
    gold = pd.read_csv(GOLD_FILE).rename(columns={"comp": "name", "Group": "gold_group"})
    TUTORIAL_LEGAL_FORMS = {   # method A of Tutorial 1, as written in the notebook
        "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "LLC", "LP", "PLC",
        "GMBH", "AG", "KG", "MBH", "SE", "SA", "SAS", "SPA", "SRL", "BV", "NV", "AB", "AS", "ASA", "OY", "OYJ",
        "KK", "KABUSHIKI", "KAISHA", "LTDA", "PTY", "THE", "AKTIENGESELLSCHAFT", "GESELLSCHAFT"}

    def tutorial_method_a(name):
        words = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper()).split()
        while words and words[-1] in TUTORIAL_LEGAL_FORMS:
            words.pop()
        while words and words[0] in TUTORIAL_LEGAL_FORMS:
            words.pop(0)
        return " ".join(words)

    g_norm = gold["name"].map(normalise_full)                                  # our layer 1
    g_table = pd.DataFrame({"name_norm": g_norm.unique()})
    g_table["applicant_type"] = [applicant_type(None, n) for n in g_table["name_norm"]]
    g_table["n_families"] = 1
    g_anchor = build_anchor_map(g_table, None)                                  # our layer 2
    methods = {
        "no harmonisation": gold["name"],
        "Tutorial 1 · method A (legal forms)": gold["name"].map(tutorial_method_a),
        "Tutorial 1 · first word of method A": gold["name"].map(lambda n: (tutorial_method_a(n).split() or [""])[0]),
        "ours · layer 1 (normalisation)": g_norm,
        "ours · layers 1+2 (+ anchor-prefix merge)": g_norm.map(g_anchor),
        "ours · layers 1+2+3 (+ corporate groups)": g_norm.map(g_anchor).map(to_group),
    }
    gold_scores = pd.DataFrame({k: harmonisation_scores(gold["gold_group"], v) for k, v in methods.items()}).T
    gold_scores.to_csv(C.OUT_STEP1 / "harmonisation_gold_standard.csv")
    print(f"{len(gold):,} names in {gold['gold_group'].nunique():,} organisations (gold standard)")
    display(gold_scores.style.format({"groups found": "{:,.0f}", "precision": "{:.3f}", "recall": "{:.3f}", "F1": "{:.3f}",
                                      "homogeneity": "{:.3f}", "completeness": "{:.3f}", "V-measure": "{:.3f}"}, na_rep="–"))
    # where layers 1+2 still split one organisation: the largest gold groups with more than one predicted name
    ours = methods["ours · layers 1+2 (+ anchor-prefix merge)"]
    split = gold.assign(pred=ours.values).groupby("gold_group").agg(names=("name", "size"), predicted_groups=("pred", "nunique"),
                                                                    example=("name", "first"))
    print("Organisations still split by layers 1+2 (largest first): translations, names not at the start, typos")
    display(split[split.predicted_groups > 1].sort_values("names", ascending=False).head(10))
else:
    print(f"{GOLD_FILE} not found: gold-standard evaluation skipped")

# %% [markdown]
# **Reading.** Removing legal forms alone (method A, our layer 1) merges almost nothing (F1 ≈ 0): the variants of the
# large groups differ by more than a legal form. Our layers 1+2 reach F1 ≈ 0.93 (precision 0.97, recall 0.89), close to
# the "first word" rule of the tutorial (F1 ≈ 0.94) but with **higher precision**: the first-word rule merges every
# applicant that starts with the same word, which in our sector would fuse different Chinese universities and firms
# (*HARBIN ENGINEERING UNIVERSITY* and *HARBIN INSTITUTE OF TECHNOLOGY*, *CHINA STATE SHIPBUILDING* and *CHINA
# NATIONAL OFFSHORE OIL*), the first players of the field. A false merge inflates concentration and invents leaders;
# a missed merge (translations, names not at the start, typos: the table above) is caught for the players that matter
# by the verified groups of layer 3 and by the review list of 7.3. These numbers do not depend on PATSTAT: they are the
# same on TEST and PROD.

# %% [markdown]
# ### 7.3 Review list and sanity check
# `applicants_to_review.csv`: the 100 largest groups, plus names sharing a first word (possible same group).

# %%
top = (applicants.groupby("applicant")
       .agg(families=("docdb_family_id", "nunique"), fractional=("weight", "sum"),
            type=("applicant_type", "first"), country=("country", "first"),
            first_year=("family_year", "min"), last_year=("family_year", "max"))
       .sort_values("families", ascending=False))
display(top.head(20))

first_word = top.index.to_series().str.split().str[0]
shared = first_word[first_word.duplicated(keep=False) & first_word.str.len().gt(3)]
review = pd.concat([
    top.head(100).assign(reason="top 100"),
    top.loc[shared.index].assign(reason="shares first word: " + shared),
]).reset_index().drop_duplicates("applicant")
review.to_csv(C.OUT_STEP1 / "applicants_to_review.csv", index=False)

# one row per applicant group: type, main country and its source, families, number of source names
profile = top.join(audit.groupby("applicant").agg(n_source_names=("base_name", "nunique"),
                                                    n_harmonised_names=("name_harmonised", "nunique")))
profile = profile.join(applicants.groupby("applicant")["country_source"].agg(lambda s: s.mode().iat[0]))
profile.to_csv(C.OUT_STEP1 / "applicants_profile.csv")
print(f"Client group ({CLIENT_GROUP}) families in the sector: "
      f"{int(applicants.loc[applicants['is_client_group'], 'docdb_family_id'].nunique())}")

found = {p: int(top.loc[p, "families"]) if p in top.index else 0 for p in C.KNOWN_PLAYERS}
display(pd.Series(found, name="families").to_frame("known players: families in the sector"))
print(f"Missing: {[p for p, v in found.items() if v == 0]} -> if many are missing, check the query and the aliases")

# %% [markdown]
# ### 7.4 Owners of every application
#
# `applicants.csv` is at family level (one row per family × group). For checks and for the ownership changes of
# Section 9.2, `application_applicants.csv` keeps **one row per application × applicant person**, with every name
# PATSTAT gives and every layer of the harmonisation: `person_name` (as printed on the document), `psn_name`
# (PATSTAT standardised name), `han_name` (OECD harmonised name), `name_norm` and `name_harmonised` (layers 1–2),
# `applicant` (corporate group, layer 3), the type, the country and its source, and `applt_seq_nr` (position in the
# list of applicants, 1 = first applicant). This is the **applicant at filing** (field 71 of the front page,
# IM_Theory_1); who owns the patent later (field 73) is in Section 9.2.

# %%
app_cols = ["appln_id", "docdb_family_id", "appln_auth", "applt_seq_nr", "person_id", "person_name", "psn_id", "psn_name",
            "han_id", "han_name", "name_norm", "name_harmonised", "applicant", "applicant_type", "country", "country_source"]
application_applicants = (appl[[c for c in app_cols if c in appl.columns]]
                          .drop_duplicates(["appln_id", "person_id"])
                          .sort_values(["docdb_family_id", "appln_id"] + (["applt_seq_nr"] if "applt_seq_nr" in appl else [])))
application_applicants["is_client_group"] = application_applicants["applicant"].eq(CLIENT_GROUP)
print(f"application x applicant rows: {len(application_applicants):,} | applications with an applicant: "
      f"{application_applicants['appln_id'].nunique():,} of {len(appln_ids):,}")

# %% [markdown]
# ## 8 · Inventors
#
# Inventor addresses show **where R&D is done**, which can differ from where the owner is registered
# (IM_Theory_1): a gap points to foreign R&D centres or acquisitions. `team_size` (distinct inventors per family)
# and co-inventor links are inputs for collaboration networks (IM_Theory_4). Many inventors (most Chinese ones) have
# no country in PATSTAT: as for applicants, the national office of the application fills the gap when
# `COUNTRY_FALLBACK_TO_OFFICE` is on, and `country_source` records it.

# %%
if C.FETCH_INVENTORS:
    inv_raw = persons(TLS207_PERS_APPLN.invt_seq_nr > 0)
    inv_raw["country"] = inv_raw["person_ctry_code"].apply(clean_country)
    inv_raw["country_source"] = np.where(inv_raw["country"].notna(), "person", "unknown")
    if COUNTRY_FALLBACK_TO_OFFICE:
        office = inv_raw["appln_auth"].astype("string").str.strip()
        use_office = inv_raw["country"].isna() & office.notna() & ~office.isin(["EP", "WO", ""])
        inv_raw.loc[use_office, "country"] = office[use_office]
        inv_raw.loc[use_office, "country_source"] = "filing_office"
    print("Inventor country source (rows):", inv_raw["country_source"].value_counts().to_dict())
    inv_raw["inventor"] = inv_raw["psn_name"].fillna(inv_raw["person_name"]).astype("string").str.upper().str.strip()
    nuts_cols = [c for c in ("nuts", "nuts_level") if c in inv_raw.columns]
    if nuts_cols:   # one NUTS code per family x inventor: the most detailed one recorded in any family member
        # The detail is read from the LENGTH of the code (2 = country, 4 = NUTS 2, 5 = NUTS 3), not from nuts_level:
        # nuts_level 4 marks codes completed from OECD REGPAT (ordinary NUTS 3 codes) and 9 marks persons with no
        # region, so sorting on nuts_level could keep an empty row before a located one (Tutorial 3, nb 31 §3).
        # Codes of older NUTS versions (e.g. FR222) are kept as they are (they still compare with themselves).
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
    by_country = pd.DataFrame({
        "applicants": applicants.groupby("country")["country_weight"].sum(),
        "inventors": inventors.groupby("country")["country_weight"].sum(),
    }).fillna(0)
    print("Fractional country shares (%), family level, all years:")
    display((by_country / by_country.sum() * 100).round(1).sort_values("applicants", ascending=False).head(10))

# %% [markdown]
# ## 9 · Legal events (`tls231_inpadoc_legal_event`)
#
# The life of a patent after filing (IM_Theory_1 and 2): renewal fees, lapses, withdrawals, oppositions,
# revocations, transfers of ownership, licences. Per family we derive:
#
# | Column | Meaning | Use |
# |---|---|---|
# | `max_renewal_year` | highest renewal year paid (`fee_renewal_year`) | value: owners renew only valuable patents (renewal models) |
# | `n_renewal_countries` | countries where fees were paid (`fee_country`) | breadth of protection actually kept |
# | `ev_lapse`, `ev_withdrawal_refusal`, `ev_revocation` | the right ended or never started | **active family** proxy (IM_Theory_3) |
# | `ev_opposition` | opposed after grant | value and risk (Harhoff, Scherer & Vopel 2003) |
# | `ev_transfer`, `ev_licence` | ownership changes, licences | ultimate owner, NPE activity, M&A (IM_Theory_2) |
#
# Events are matched with regular expressions (`config.LEGAL_EVENT_PATTERNS`) on the **tls803 description and category** of each event code, plus the tls231 event text. The description is needed: for many offices (all CN events) `event_text` is empty, and the EP free text "NO OPPOSITION FILED" must not count as an opposition. EP renewals are the `PGFP` events (Tutorial 0, example 5).
# `legal_event_codes.csv` lists the codes that appear with their descriptions (`tls803`): **check them** before
# using a flag. Coverage depends on the office (best for EP and the major offices).

# %%
if C.FETCH_LEGAL_EVENTS and TLS231 is not None:
    ev_cols = available_columns(TLS231, ["appln_id", "event_seq_nr", "event_auth", "event_code", "event_publn_date",
                                         "event_effective_date", "event_category_code", "event_category_title",
                                         "event_text", "fee_country", "fee_renewal_year", "lapse_country",
                                         "party_type", "party_new", "party_old"])
    events = fetch(lambda ch: db.query(*ev_cols).filter(TLS231.appln_id.in_(ch)), appln_ids)
    if not events.empty:
        events = events.merge(apps[["appln_id", "docdb_family_id"]], on="appln_id", how="left")
        events["event_code"] = events["event_code"].astype(str).str.strip()
        codes = events.groupby(["event_auth", "event_code"]).size().rename("n").reset_index()
        if TLS803 is not None:
            c803 = available_columns(TLS803, ["event_auth", "event_code", "event_descr", "event_category_code",
                                              "event_category_title"])
            desc = pd.DataFrame(patstat.df(db.query(*c803).filter(TLS803.event_code.in_(codes["event_code"].unique().tolist()))))
            if not desc.empty:
                desc["event_code"] = desc["event_code"].astype(str).str.strip()
                codes = codes.merge(desc, on=[c for c in ["event_auth", "event_code"] if c in desc.columns], how="left")
        codes.sort_values("n", ascending=False).to_csv(C.OUT_STEP1 / "legal_event_codes.csv", index=False)

        # flags on the tls803 DESCRIPTION + category: tls231 event_text is empty for many offices (e.g. all CN events),
        # and its free text says "NO OPPOSITION FILED" (config.LEGAL_EVENT_PATTERNS handles the negative cases)
        desc_cols = [c for c in ["event_descr", "event_category_title"] if c in codes.columns]
        if desc_cols:
            events = events.drop(columns=[c for c in desc_cols if c in events.columns]).merge(
                codes[["event_auth", "event_code"] + desc_cols], on=["event_auth", "event_code"], how="left")
        text = events.get("event_descr", pd.Series("", index=events.index)).fillna("").astype(str) + " | " + \
            events.get("event_category_title", pd.Series("", index=events.index)).fillna("").astype(str) + " | " + \
            events.get("event_text", pd.Series("", index=events.index)).fillna("").astype(str)
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
        ev_flag_cols = list(flags.columns)
        families[ev_flag_cols] = families[ev_flag_cols].fillna(False).astype(bool)
        # proxy of an active family: at least one application without an event that ends the right
        # (lapse, withdrawal/refusal, revocation). Rough: a lapse can concern one validation country only.
        ended = events.groupby("appln_id")[["ev_lapse", "ev_withdrawal_refusal", "ev_revocation"]].any().any(axis=1)
        app_alive = apps[["appln_id", "docdb_family_id"]].assign(alive=~apps["appln_id"].map(ended).fillna(False).astype(bool))
        families = families.merge(app_alive.groupby("docdb_family_id")["alive"].any().rename("active_proxy").reset_index(),
                                  on="docdb_family_id", how="left")
        print(f"legal events: {len(events):,} | families with events: {fam_ev.shape[0]:,}")
        display(families[ev_flag_cols].mean().mul(100).round(1).to_frame("% of families"))

# %% [markdown]
# ### 9.1 Patent life cycle: every date, and the status of each patent today
#
# IM_Theory_1 follows a patent from filing to expiry: **priority → filing (22) → search report → publication (43, ~18
# months) → grant (45) → opposition window (9 months, EPO) → renewals → lapse, withdrawal, refusal, revocation or
# expiry (20 years from filing)**. The application number (`appln_id`) is the stable key that links all the
# publications (A1, A2, A3, B1, B2…) of one application: every date below is attached to the application, then
# summarised per family.
#
# **Application level** (added to `applications.csv`):
#
# | Column | Source | Meaning |
# |---|---|---|
# | `priority_date` | tls201 `earliest_filing_date` | first filing of the invention anywhere (closest to the invention) |
# | `appln_filing_date` | tls201 | filing of this application (field 22); the 20-year term runs from here |
# | `months_priority_to_filing` | computed | extension lag: about 12 for Paris-route filings, about 30–31 for PCT national phases |
# | `first_publn_date` | tls211 (min `publn_date`) or tls201 `earliest_publn_date` | first publication (field 43) |
# | `search_report_publn_date` | tls211, EP kind A1 or A3 | publication of the European search report |
# | `grant_date`, `grant_kind` | tls211 `publn_first_grant = Y` | grant (field 45) |
# | `amended_after_opposition_date` | tls211 EP kind B2 | patent maintained in amended form after opposition |
# | `limitation_date` | tls211 EP kind B3 | patent limited at the owner's request |
# | `opposition_deadline` | grant date + 9 months (EP only) | end of the EPO opposition window |
# | `pct_national_phase_deadline` | priority + 30 months (WO only) | deadline to choose the countries (31 months at the EPO) |
# | `expiry_date` | filing date + 20 years | theoretical end of protection if every renewal fee is paid (no US term adjustments or SPCs) |
# | `last_renewal_date`, `max_renewal_year` | tls231 renewal events | last renewal fee recorded |
# | `end_date`, `end_type` | tls231 | first event that ends the right **for the whole application**: lapsed, withdrawn, refused, revoked |
# | `n_lapsed_countries`, `last_country_lapse_date` | tls231 (EP patents) | EP patents lapse **country by country** after grant: these events do not end the EP patent as a whole. The PG25 description does not say whether the country was never validated (no translation) or lapsed later (fees not paid), so read it as "countries where the EP patent is no longer in force" |
# | `status`, `residual_life_years` | computed at `AS_OF` | pending / in force / ended / expired / PCT request; years of protection left if in force |
# | `months_filing_to_publn`, `years_filing_to_grant` | computed | speed of the procedure |
#
# **Family level** (added to `families.csv`): `priority_date`, `first_filing_date`, `first_publn_date`,
# `first_grant_date` and `first_grant_office`, `last_grant_date`, `n_granted_offices`, `ep_grant_date`,
# `ep_opposition_deadline`, `years_priority_to_first_grant` (the precise version of `grant_lag`), `family_status`
# (*in force* if at least one application is in force, *pending* if none is in force but one is still pending,
# *dead* otherwise), `family_expiry_date` and `family_residual_life_years` (latest expiry among the applications in
# force), `family_end_date` (when the last right ended, for dead families), `n_in_force_offices`.
#
# `publications.csv` keeps one row per publication (kind code, date, first-grant flag) for any other check.
#
# **Reference date.** `AS_OF` is the latest legal-event publication date in the data (the PATSTAT edition), not today:
# the status is what PATSTAT knows at that date. Office actions (e.g. the Japanese *notification of reasons for
# refusal*) are not ends; a refusal or withdrawal recorded on an application that was later granted is ignored.
# **Caveats** for the appendix: missing dates in PATSTAT (9999-12-31)
# are left empty; an application with no decision and no end event after many years is probably dead without a
# recorded event (`pending, no decision recorded`); US terms can differ from 20 years (patent term adjustment,
# continuations), and supplementary protection certificates are ignored.

# %%
def to_date(s):
    """PATSTAT dates -> datetime; the 9999-12-31 placeholder becomes missing."""
    d = pd.to_datetime(pd.Series(s), errors="coerce")
    return d.where(d.dt.year < C.MISSING_YEAR)

def months_between(a, b):
    return (b - a).dt.days / 30.4375

# ---------------------------------------------------------------- 1. tls201 dates of every application
c201 = available_columns(TLS201_APPLN, ["appln_id", "earliest_filing_date", "earliest_publn_date", "appln_filing_date"])
d201 = fetch(lambda ch: db.query(*c201).filter(TLS201_APPLN.appln_id.in_(ch)), appln_ids, ["appln_id"])
life = apps[["appln_id", "docdb_family_id", "appln_auth", "appln_kind", "appln_filing_date"]].drop_duplicates("appln_id").copy()
life = life.merge(d201.drop(columns=[c for c in ["appln_filing_date"] if c in d201.columns]).drop_duplicates("appln_id"),
                  on="appln_id", how="left")
life["appln_filing_date"] = to_date(life["appln_filing_date"])
life["priority_date"] = to_date(life.get("earliest_filing_date", pd.Series(pd.NaT, index=life.index)))
life["priority_date"] = life["priority_date"].fillna(life["appln_filing_date"])
life["first_publn_date"] = to_date(life.get("earliest_publn_date", pd.Series(pd.NaT, index=life.index)))
life = life.drop(columns=[c for c in ["earliest_filing_date", "earliest_publn_date"] if c in life.columns])
life["is_pct_request"] = life["appln_auth"].eq("WO") & life["appln_kind"].astype(str).str.startswith("W")

# ---------------------------------------------------------------- 2. publication dates (tls211)
if not publications.empty and "publn_date" in publications:
    pub = publications.copy()
    pub["publn_date"] = to_date(pub["publn_date"])
    pub["publn_kind"] = pub.get("publn_kind", pd.Series("", index=pub.index)).astype(str).str.strip()
    pub["publn_auth"] = pub.get("publn_auth", pd.Series("", index=pub.index)).astype(str).str.strip()
    first_grant_flag = pub.get("publn_first_grant", pd.Series("N", index=pub.index)).astype(str).str.strip().eq("Y")
    g = pub.groupby("appln_id")
    life = life.merge(g["publn_date"].min().rename("first_publn_date_211"), on="appln_id", how="left")
    life["first_publn_date"] = life["first_publn_date"].fillna(life.pop("first_publn_date_211"))
    gr = pub[first_grant_flag].sort_values("publn_date").drop_duplicates("appln_id")
    life = life.merge(gr[["appln_id", "publn_date", "publn_kind"]].rename(columns={"publn_date": "grant_date", "publn_kind": "grant_kind"}),
                      on="appln_id", how="left")
    ep = pub[pub["publn_auth"].eq("EP")]
    for col, kinds in [("search_report_publn_date", ["A1", "A3"]), ("amended_after_opposition_date", ["B2"]),
                       ("limitation_date", ["B3"])]:
        life = life.merge(ep[ep["publn_kind"].isin(kinds)].groupby("appln_id")["publn_date"].min().rename(col),
                          on="appln_id", how="left")
    pub.drop(columns=[c for c in ["publn_year"] if c in pub.columns]).to_csv(C.OUT_STEP1 / "publications.csv", index=False)
else:
    for col in ["grant_date", "grant_kind", "search_report_publn_date", "amended_after_opposition_date", "limitation_date"]:
        life[col] = pd.NaT if col != "grant_kind" else None
life["grant_date"] = pd.to_datetime(life["grant_date"])
granted_flag = apps.drop_duplicates("appln_id").set_index("appln_id")["granted"].eq("Y")
life["is_granted"] = life["appln_id"].map(granted_flag).fillna(False).astype(bool) | life["grant_date"].notna()

# ---------------------------------------------------------------- 3. deadlines and theoretical expiry
life["opposition_deadline"] = (life["grant_date"] + pd.DateOffset(months=9)).where(life["appln_auth"].eq("EP"))
life["pct_national_phase_deadline"] = (life["priority_date"] + pd.DateOffset(months=30)).where(life["is_pct_request"])
life["expiry_date"] = (life["appln_filing_date"] + pd.DateOffset(years=20)).where(~life["is_pct_request"])

# ---------------------------------------------------------------- 4. legal events: renewals and end of the right
END_TYPES = [("revoked", r"\bREVOK|\bREVOCATION\b(?!.*BEFORE THE COURT)|PATENT REVOKED"),
             ("refused", r"REJECT|REFUS"),
             ("withdrawn", r"WITHDRAW|ABANDON|DISCONTINU"),
             ("lapsed", r"LAPSE|CEASED|CESSATION|TERMINATION OF PATENT RIGHT|EXPIR|NON-PAYMENT|FAILURE TO PAY|NOT PAYING")]
NOT_AN_END = (r"REVIVAL|REINSTATE|RE-ESTABLISH|RESTORATION|CLAIM OR COUNTERCLAIM|OPT-OUT|INTENTION|INTENDED|REQUEST FOR|"
              r"NOTICE OF|NOTIFICATION OF REASON|DECISION OF GRANT OR REJECTION|APPEAL|OPPOSITION (?:WAS )?(?:REJECTED|WITHDRAWN)|"
              r"TRANSFER WITHDRAWN|INFORMATION DELETED|MODIFIED|REMAND|DOMESTIC PRIORITY|EXPIRATION OF 19TH MONTH")   # office actions, not ends
COUNTRY_LEVEL = r"CONTRACTING STATE|^[A-Z]{2}: |INVALIDATED EUROPEAN PATENT|INVALIDATION OF EXTENSION|TRANSLATION"
ev_ok = C.FETCH_LEGAL_EVENTS and TLS231 is not None and "events" in globals() and not events.empty
if ev_ok:
    e = events.merge(apps[["appln_id", "appln_auth"]].drop_duplicates("appln_id"), on="appln_id", how="left").copy()
    e["date"] = to_date(e.get("event_effective_date", pd.Series(pd.NaT, index=e.index))).fillna(
        to_date(e.get("event_publn_date", pd.Series(pd.NaT, index=e.index))))
    AS_OF = to_date(e.get("event_publn_date", pd.Series(pd.NaT, index=e.index))).max()
    descr = e.get("event_descr", pd.Series("", index=e.index)).fillna("").astype(str).str.upper()
    e["end_type"] = None
    for name, pat in END_TYPES[::-1]:                       # the first pattern in the list wins
        e.loc[descr.str.contains(pat, regex=True), "end_type"] = name
    e.loc[descr.str.contains(NOT_AN_END, regex=True), "end_type"] = None
    country_level = (descr.str.contains(COUNTRY_LEVEL, regex=True)
                     | (e["appln_auth"].eq("EP") & e["event_auth"].astype(str).str.strip().ne("EP")))
    central = e[e["end_type"].notna() & ~country_level].sort_values("date").drop_duplicates("appln_id")
    life = life.merge(central[["appln_id", "date", "end_type"]].rename(columns={"date": "end_date"}), on="appln_id", how="left")
    # a granted application was not refused or withdrawn in the end (appeal, divisional, re-examination): keep only
    # the ends that can follow a grant (lapse, revocation)
    overruled = life["is_granted"] & life["end_type"].isin(["refused", "withdrawn"]) & ~(life["end_date"] > life["grant_date"])
    life.loc[overruled, ["end_date", "end_type"]] = [pd.NaT, None]
    lapsed_c = e[e["end_type"].notna() & country_level & e["appln_auth"].eq("EP")].copy()
    lapsed_c["country"] = lapsed_c.get("lapse_country", pd.Series("", index=lapsed_c.index)).astype(str).str.strip()
    lapsed_c.loc[lapsed_c["country"].isin(["", "nan", "None"]), "country"] = lapsed_c["event_auth"].astype(str).str.strip()
    life = life.merge(lapsed_c.groupby("appln_id").agg(n_lapsed_countries=("country", "nunique"),
                                                        last_country_lapse_date=("date", "max")).reset_index(),
                      on="appln_id", how="left")
    ren = e[e.get("ev_renewal_fee", pd.Series(False, index=e.index)).astype(bool)]
    life = life.merge(ren.groupby("appln_id").agg(last_renewal_date=("date", "max")).reset_index(), on="appln_id", how="left")
    if "fee_renewal_year" in ren:
        life = life.merge(pd.to_numeric(ren["fee_renewal_year"], errors="coerce").groupby(ren["appln_id"]).max()
                          .rename("max_renewal_year").reset_index(), on="appln_id", how="left")
else:
    AS_OF = pd.Timestamp.today().normalize()
    for col in ["end_date", "last_country_lapse_date", "last_renewal_date"]:
        life[col] = pd.NaT
    life["end_type"], life["n_lapsed_countries"], life["max_renewal_year"] = None, np.nan, np.nan
for col in ["end_date", "last_country_lapse_date", "last_renewal_date"]:
    life[col] = pd.to_datetime(life[col])
life["n_lapsed_countries"] = life["n_lapsed_countries"].fillna(0).astype(int)

# ---------------------------------------------------------------- 5. status today and residual life
ended = life["end_date"].notna() & (life["end_date"] <= AS_OF)
expired = life["expiry_date"].notna() & (life["expiry_date"] <= AS_OF)
life["status"] = np.select(
    [life["is_pct_request"], ended, expired, life["is_granted"],
     (AS_OF - life["appln_filing_date"]).dt.days > 10 * 365.25],
    ["PCT request", "ended: " + life["end_type"].fillna("").astype(str), "expired", "in force",
     "pending, no decision recorded"], default="pending")
life["residual_life_years"] = np.where(life["status"].eq("in force"), (life["expiry_date"] - AS_OF).dt.days / 365.25, np.nan)
life["months_priority_to_filing"] = months_between(life["priority_date"], life["appln_filing_date"]).round(1)
life["months_filing_to_publn"] = months_between(life["appln_filing_date"], life["first_publn_date"]).round(1)
life["years_filing_to_grant"] = ((life["grant_date"] - life["appln_filing_date"]).dt.days / 365.25).round(2)

DATE_COLS = ["priority_date", "appln_filing_date", "first_publn_date", "search_report_publn_date", "grant_date",
             "amended_after_opposition_date", "limitation_date", "opposition_deadline", "pct_national_phase_deadline",
             "expiry_date", "last_renewal_date", "end_date", "last_country_lapse_date"]
LIFE_COLS = DATE_COLS + ["grant_kind", "end_type", "n_lapsed_countries", "max_renewal_year", "status", "residual_life_years",
                         "months_priority_to_filing", "months_filing_to_publn", "years_filing_to_grant"]
applications = applications.drop(columns=[c for c in LIFE_COLS if c in applications.columns and c != "appln_filing_date"])
applications["appln_filing_date"] = to_date(applications["appln_filing_date"])
applications = applications.merge(life[["appln_id"] + [c for c in LIFE_COLS if c != "appln_filing_date"]].rename(
    columns={"max_renewal_year": "max_renewal_year_appln"}), on="appln_id", how="left")

# ---------------------------------------------------------------- 6. family summary
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
    "ep_grant_date": grants[grants["appln_auth"].eq("EP")].groupby("docdb_family_id")["grant_date"].min(),
    "n_in_force_offices": in_force.groupby("docdb_family_id")["appln_auth"].nunique(),
    "family_expiry_date": in_force.groupby("docdb_family_id")["expiry_date"].max(),
    "family_residual_life_years": in_force.groupby("docdb_family_id")["residual_life_years"].max(),
    "last_end_date": lf.assign(end=lf["end_date"].where(ended.loc[lf.index], lf["expiry_date"].where(expired.loc[lf.index])))
                       .groupby("docdb_family_id")["end"].max(),
})
fam_life.index.name = "docdb_family_id"
fam_life["ep_opposition_deadline"] = fam_life["ep_grant_date"] + pd.DateOffset(months=9)
fam_life["years_priority_to_first_grant"] = ((fam_life["first_grant_date"] - fam_life["priority_date"]).dt.days / 365.25).round(2)
fam_life["n_granted_offices"] = fam_life["n_granted_offices"].fillna(0).astype(int)
fam_life["n_in_force_offices"] = fam_life["n_in_force_offices"].fillna(0).astype(int)
has_pending = pending.groupby("docdb_family_id").size().reindex(fam_life.index).notna()
fam_life["family_status"] = np.select([fam_life["n_in_force_offices"] > 0, has_pending], ["in force", "pending"], default="dead")
fam_life["family_end_date"] = fam_life["last_end_date"].where(fam_life["family_status"].eq("dead"))
fam_life = fam_life.drop(columns="last_end_date").reset_index()
families = families.drop(columns=[c for c in fam_life.columns if c in families.columns and c != "docdb_family_id"])
families = families.merge(fam_life, on="docdb_family_id", how="left")

print(f"reference date AS_OF = {pd.Timestamp(AS_OF).date()} | applications with: "
      + ", ".join(f"{c} {life[c].notna().mean():.0%}" for c in ["priority_date", "first_publn_date", "grant_date", "expiry_date", "end_date"]))
display(life["status"].value_counts().to_frame("applications"))
display(families["family_status"].value_counts().to_frame("families"))
display(pd.DataFrame({
    "median months priority -> filing (non-PCT, non-first filings)": [lf.loc[lf["months_priority_to_filing"] > 0.5, "months_priority_to_filing"].median()],
    "median months filing -> first publication": [lf["months_filing_to_publn"].median()],
    "median years filing -> grant": [lf["years_filing_to_grant"].median()],
    "median residual life of families in force (years)": [families["family_residual_life_years"].median()],
}).T.rename(columns={0: "value"}).round(2))
display(lf.groupby("appln_auth").agg(applications=("appln_id", "size"), granted=("is_granted", "mean"),
                                     median_years_to_grant=("years_filing_to_grant", "median"),
                                     in_force_today=("status", lambda s: s.eq("in force").mean()))
          .sort_values("applications", ascending=False).head(10).round(2))

# %% [markdown]
# ### 9.2 Ownership changes and the current owner
#
# The applicant at filing (field 71) is not necessarily the owner today (field 73): patents are sold, merged into
# other companies, licensed and pledged as collateral (IM_Theory_1, *"ownership can change later through sales and
# licences, recorded as legal events"*; IM_Theory_2, the secondary market for technologies). The WIPO landscape of
# IM_Theory_3 counts patents by their **ultimate owner** for this reason. The transfer events of tls231 (`ev_transfer`,
# `party_old`, `party_new`) are classified, because many of them are **not** changes of owner:
#
# | Category | Recognised by | Changes the owner? |
# |---|---|---|
# | `procedural` | *transfer to examiner*, *transfer withdrawn*, *request for…* (JP, KR) | no |
# | `security` | *security interest / agreement*, *lien*, *release by secured party* (US) | no: the patent is collateral for a loan (a risk to note in Step 6) |
# | `licence` | *license*, *confirmatory license* | no (licences are counted in `ev_licence`) |
# | `rename` | *change of name / address* | no, same entity (unless the new name is another group) |
# | `correction` | *corrective*, *nunc pro tunc* | no |
# | `merger` | *merger* | yes, if the new owner is another group |
# | `assignment` | everything else (*assignment of assignors interest*, *transfer of patent right*, *change of applicant/patentee*, *party data changed*) | yes, **if the new owner is a different group** from the applicants at filing |
#
# The new owner (`party_new`) goes through the same harmonisation as the applicants: layer 1 normalisation, layer 2
# anchors when the name is already in the data, layer 3 corporate groups. A change is **real** when the new owner's
# group is not among the groups that filed the application: this removes the routine US assignment from the inventors
# to their employer (the employer is already the applicant) and the renaming of the same company. Some offices (JP,
# KR, and almost all CN transfers) record a change of owner without the name of the new owner: it counts as a change with
# **unknown** new owner. The change itself is still informative: for example, transfers out of Chinese universities are
# technology transfer to firms (IM_Theory_2).
#
# Outputs: `ownership_events.csv` (every transfer-type event, classified), `current_owners.csv` (one row per
# application: owners at filing, current owners, number of real changes, dates, security interest, licence recorded),
# `family_current_owners.csv` (one row per family × current owner group, with 1/n weights, to attribute portfolios
# to their owner today in Step 3), and in `families.csv`: `owner_changed`, `n_owner_changes`,
# `first_owner_change_date`, `last_owner_change_date`, `current_owners`, `owner_change_from_individuals`,
# `has_security_interest`. **Coverage** depends on the office: best for US, CN, EP and DE.

# %%
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
OWNER_CATEGORIES = [   # first match wins
    ("procedural", r"TRANSFER TO EXAMINER|TRANSFER OF A CASE|RECONSIDERATION|TRANSFER WITHDRAWN|REQUEST FOR"),
    ("security", r"SECURITY|\bLIEN\b|PLEDGE|RELEASE BY SECURED|RELEASE OF SECURITY|PATENT RELEASE|RELEASE OF .*LIEN"),
    ("licence", r"LICEN[CS]E"),
    ("correction", r"CORRECTIVE|NUNC PRO TUNC|CORRECTION"),
    ("merger", r"MERGER"),
    ("rename", r"CHANGE OF NAME|NAME CHANGE|CHANGE OF ADDRESS"),
]

def owner_name(party):
    """party_new -> a name: drop the trailing ', STATE' / ', XX' added by US and DE records."""
    if pd.isna(party) or not str(party).strip():
        return None
    s = re.sub(r"\s+", " ", str(party).upper()).strip()
    parts = [p.strip() for p in s.split(",")]
    while len(parts) > 1 and (parts[-1] in US_STATES or parts[-1] in COUNTRY_WORDS or re.fullmatch(r"[A-Z]{2}", parts[-1])
                              or re.fullmatch(r"\d{4,6}.*", parts[-1])):
        parts = parts[:-1]
    return ", ".join(parts)

def owner_group(name):
    if not name:
        return None
    norm = normalise_full(name)
    # US records sometimes append the state without a comma ("... INSTITUTE OF TECHNOLOGY MASSACHUSETTS"):
    # drop it when the shorter name is an applicant of the data
    for place in sorted(US_STATES | COUNTRY_WORDS, key=len, reverse=True):
        if norm.endswith(" " + place) and norm[: -len(place) - 1] in anchor_map:
            norm = norm[: -len(place) - 1]
            break
    return to_group(anchor_map.get(norm, norm)) or None

own_ok = C.FETCH_LEGAL_EVENTS and TLS231 is not None and "events" in globals() and not events.empty
if own_ok:
    t = events[events["ev_transfer"]].copy()
    t["date"] = to_date(t.get("event_effective_date", pd.Series(pd.NaT, index=t.index))).fillna(
        to_date(t.get("event_publn_date", pd.Series(pd.NaT, index=t.index))))
    text = (t.get("party_old", pd.Series("", index=t.index)).fillna("").astype(str) + " | "
            + t.get("event_descr", pd.Series("", index=t.index)).fillna("").astype(str)).str.upper()
    t["category"] = "assignment"
    for name, pat in OWNER_CATEGORIES[::-1]:
        t.loc[text.str.contains(pat, regex=True), "category"] = name
    t["new_owner_name"] = t.get("party_new", pd.Series(None, index=t.index)).apply(owner_name)
    t["new_owner"] = t["new_owner_name"].map({n: owner_group(n) for n in t["new_owner_name"].dropna().unique()})
    filed_by = application_applicants.groupby("appln_id")["applicant"].agg(lambda s: set(s.dropna()))
    t["filed_by"] = t["appln_id"].map(filed_by.to_dict()).apply(lambda s: s if isinstance(s, set) else set())
    t["is_real_change"] = (t["category"].isin(["assignment", "merger"])
                           & (t["new_owner"].isna() | ~pd.Series([o in f for o, f in zip(t["new_owner"], t["filed_by"])],
                                                                  index=t.index, dtype=bool)))
    t["new_owner"] = t["new_owner"].where(t["new_owner"].notna() | ~t["is_real_change"], "UNKNOWN (not recorded)")
    t["from_individuals"] = t["appln_id"].map(
        application_applicants.groupby("appln_id")["applicant_type"].agg(lambda s: set(s) == {"individual"}).to_dict()).fillna(False).astype(bool)
    t["publn_date"] = to_date(t.get("event_publn_date", pd.Series(pd.NaT, index=t.index)))
    keep = ["appln_id", "docdb_family_id", "event_auth", "event_code", "date", "publn_date", "event_descr", "category", "party_old",
            "party_new", "new_owner_name", "new_owner", "is_real_change", "from_individuals"]
    ownership_events = t[[c for c in keep if c in t.columns]].assign(
        filed_by=t["filed_by"].apply(lambda s: " | ".join(sorted(s)))).sort_values(["docdb_family_id", "appln_id", "date"])
    ownership_events.to_csv(C.OUT_STEP1 / "ownership_events.csv", index=False)

    # ------------------------------------------------ current owner of each application
    real = ownership_events[ownership_events["is_real_change"]]
    # some offices (CN) write one transfer as several rows (e.g. codes ASS and C41), only one of them with the new
    # owner: drop the unnamed rows of a change that is named on the same application and publication date.
    # Most CN transfers carry no name at all in tls231: they stay as changes with an unknown new owner.
    named = real.loc[real["new_owner"].ne("UNKNOWN (not recorded)"), ["appln_id", "publn_date"]].drop_duplicates()
    dup = real.merge(named.assign(_named=True), on=["appln_id", "publn_date"], how="left")["_named"].fillna(False).astype(bool).to_numpy()
    real = real[~(dup & real["new_owner"].eq("UNKNOWN (not recorded)").to_numpy(dtype=bool))]
    last_real = real.sort_values("date").groupby("appln_id")
    last_date = last_real["date"].max()
    # owners after the latest real change: all the new owners recorded on that date
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

    # ------------------------------------------------ current owners of each family (latest real change in the family)
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
    display(t.groupby(["event_auth", "category"]).size().unstack(fill_value=0).loc[lambda d: d.sum(axis=1).sort_values(ascending=False).index].head(8))
    flows = (real.rename(columns={"filed_by": "seller"})
                 .drop_duplicates(["docdb_family_id", "seller", "new_owner"])
                 .groupby(["seller", "new_owner"]).size().rename("families").sort_values(ascending=False).head(15))
    display(flows.to_frame())
else:
    print("legal events not fetched: ownership changes skipped (owners at filing are in applicants.csv)")

# %% [markdown]
# ## 10 · Benchmarks for Step 1
#
# A growing sector is not special if all patenting grows as fast (Tutorial 0, Notebook 2; IM_Theory_3: shares
# "control for the general growth of patent filings"). We count, inside the database, families per earliest
# priority year for **all patents** and for a **neighbouring field** (`BENCHMARK_CPC`, ships and marine technology),
# also useful to argue whether the sector is patent-heavy relative to its neighbours (patents vs secrecy).

# %%
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


if C.FETCH_BENCHMARK:
    bench = families.groupby("family_year").size().rename(C.SECTOR_NAME).to_frame()
    bench = bench.join(families_per_year().set_index("year")["families"].rename("all patents"), how="outer")
    bench = bench.join(families_per_year(C.BENCHMARK_CPC).set_index("year")["families"]
                       .rename("neighbour " + "/".join(C.BENCHMARK_CPC)), how="outer").fillna(0).sort_index()
    bench["sector share of all (per mille)"] = bench[C.SECTOR_NAME] / bench["all patents"] * 1000
    bench["is_truncated"] = bench.index > C.LAST_COMPLETE_YEAR
    bench.index.name = "year"
    bench.to_csv(C.OUT_STEP1 / "benchmark_families_per_year.csv")
    display(bench.tail(12))

    # application level: applications per filing year (Tutorial 0 counts applications by appln_filing_year)
    def applications_per_year(cpc_prefixes=None):
        year = TLS201_APPLN.appln_filing_year
        q = db.query(year.label("year"), func.count(func.distinct(TLS201_APPLN.appln_id)).label("applications")).filter(
            TLS201_APPLN.ipr_type == C.IPR_TYPE, year.between(C.YEAR_MIN, C.YEAR_MAX),
            TLS201_APPLN.appln_id > 0, TLS201_APPLN.appln_id < 900000000)        # no dummy applications
        if cpc_prefixes:
            q = q.join(TLS224_APPLN_CPC, TLS224_APPLN_CPC.appln_id == TLS201_APPLN.appln_id).filter(
                func.replace(TLS224_APPLN_CPC.cpc_class_symbol, " ", "").like(cpc_prefixes[0] + "%"))
        return pd.DataFrame(patstat.df(q.group_by(year))).set_index("year")["applications"]

    in_window = applications[applications["in_appln_window"]]
    bench_app = pd.DataFrame({
        C.SECTOR_NAME: in_window.groupby("appln_filing_year")["appln_id"].nunique(),
        "all patents": applications_per_year(),
        "neighbour " + "/".join(C.BENCHMARK_CPC): applications_per_year(C.BENCHMARK_CPC),
    }).fillna(0).sort_index()
    bench_app.index.name = "year"
    bench_app["is_truncated"] = bench_app.index > C.LAST_COMPLETE_YEAR
    bench_app.to_csv(C.OUT_STEP1 / "benchmark_applications_per_year.csv")

# %% [markdown]
# ## 11 · Quality checks

# %%
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
display(pd.Series(checks, name="ok").to_frame())

abs_en = families["abstract_en"].fillna("").str.strip()
print(f"English abstract: {abs_en.ne('').sum():,} | none (kept in counts, excluded from NLP): {abs_en.eq('').sum():,}")
display(families.loc[abs_en.eq(""), ["docdb_family_id", "title", "first_office"]].head(10))
print(f"Families without applicant: {len(set(family_ids) - set(applicants['docdb_family_id'])):,}")
print(f"Applicants named UNKNOWN: {(applicants['applicant'] == 'UNKNOWN').sum():,}")
print(f"Families with no applicant country (left out of country shares): "
      f"{(~families['docdb_family_id'].isin(applicants.loc[applicants['country_weight'].notna(), 'docdb_family_id'])).sum():,}")
display(families.groupby("family_year").size().to_frame("families").T)

# %% [markdown]
# ## 12 · Save
# Bulk extracts stay on TIP (`outputs/` is git-ignored, PW kick-off: "commit the queries, not the data").

# %%
families.to_csv(C.OUT_STEP1 / "families.csv", index=False)
family_cpc.to_csv(C.OUT_STEP1 / "family_cpc.csv", index=False)
family_subclass.to_csv(C.OUT_STEP1 / "family_subclass.csv", index=False)
application_subclass.to_csv(C.OUT_STEP1 / "application_subclass.csv", index=False)
applications.drop(columns=["granted"]).to_csv(C.OUT_STEP1 / "applications.csv", index=False)
applicant_countries_by_application.to_csv(C.OUT_STEP1 / "applicant_countries_by_application.csv", index=False)
applicants.to_csv(C.OUT_STEP1 / "applicants.csv", index=False)
application_applicants.to_csv(C.OUT_STEP1 / "application_applicants.csv", index=False)
fwd.to_csv(C.OUT_STEP1 / "citations_forward.csv", index=False)
bwd.to_csv(C.OUT_STEP1 / "citations_backward.csv", index=False)
print(f"Saved in {C.OUT_STEP1.resolve()}")
print("families.csv columns:", ", ".join(families.columns))
