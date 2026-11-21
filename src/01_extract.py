"""Step 0: sector definition and extraction for underwater drones (PATSTAT on TIP).

Run from the repository root:  python src/01_extract.py [--retrieval] [--no-sbert] [--no-label-samples]
"""
from __future__ import annotations

import argparse
import os
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(SRC))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch
from sqlalchemy import func, or_

from epo.tipdata.patstat import PatstatClient
from epo.tipdata.patstat.database.models import (
    TLS201_APPLN,
    TLS202_APPLN_TITLE,
    TLS203_APPLN_ABSTR,
    TLS209_APPLN_IPC,
    TLS224_APPLN_CPC,
)

import config as C
from pw_utils import (
    BLOCKS, EXCLUSIONS, apply_blocks, compile_any, fetch_by_ids, flags_by_family, grey_zone_flags,
    precision_table, regex_to_like_phrases, summarise_blocks, wilson,
)

DISCOVERY_INCLUDE_BROAD = True
DISCOVERY_MAX_POOL = 20000
DISCOVERY_TOP_K = 40


def show(obj, title: str | None = None, digits: int = 3) -> None:
    if title:
        print(f"\n{title}")
    print(obj.round(digits).to_string(max_rows=40) if isinstance(obj, pd.DataFrame) else obj)


def stripped(col):
    return func.replace(col, " ", "")


def sql_prefix_any(col, prefixes):
    s = stripped(col)
    return or_(*(s.like(p.replace(" ", "") + "%") for p in prefixes))


def sql_like_any(col, phrases):
    low = func.lower(col)
    return or_(*(low.like(f"%{p}%") for p in phrases))


def real_pi(q):
    return q.filter(TLS201_APPLN.ipr_type == C.IPR_TYPE,
                    TLS201_APPLN.appln_id > 0, TLS201_APPLN.appln_id < C.INVALID_APPLN_ID)


def fetch_by_family(patstat, make_query, family_ids):
    return fetch_by_ids(patstat, make_query, family_ids)


def load_labels() -> pd.DataFrame:
    frames = []
    for f in sorted(C.LABEL_DIR.glob("*.csv")):
        d = pd.read_csv(f, encoding="utf-8-sig")
        if C.LABEL_COL not in d.columns or "docdb_family_id" not in d.columns:
            print(f"skipped {f.name}: needs docdb_family_id and {C.LABEL_COL}")
            continue
        d = d[pd.to_numeric(d[C.LABEL_COL], errors="coerce").isin([0, 1])].copy()
        d[C.LABEL_COL] = d[C.LABEL_COL].astype(int)
        broad = (pd.to_numeric(d[C.LABEL_COL_BROAD], errors="coerce") if C.LABEL_COL_BROAD in d
                 else pd.Series(np.nan, d.index))
        d[C.LABEL_COL_BROAD] = np.maximum(broad.fillna(d[C.LABEL_COL]), d[C.LABEL_COL]).astype(int)
        d["docdb_family_id"] = d["docdb_family_id"].astype("int64")
        d["label_file"] = f.name
        frames.append(d)
    cols = ["docdb_family_id", C.LABEL_COL, C.LABEL_COL_BROAD, "label_file", "sample", "grey_zone", "label_b"]
    if not frames:
        return pd.DataFrame(columns=cols)
    out = pd.concat(frames, ignore_index=True).drop_duplicates("docdb_family_id", keep="last")
    for c in ["sample", "grey_zone"]:
        if c not in out:
            out[c] = ""
        out[c] = out[c].fillna("").astype(str).str.strip()
    return out.reset_index(drop=True)


def load_second_labels() -> pd.DataFrame:
    out = None
    files = sorted(C.SECOND_LABEL_DIR.glob("*.csv")) if C.SECOND_LABEL_DIR.exists() else []
    for f in files:
        d = pd.read_csv(f, encoding="utf-8-sig")
        if C.LABEL_COL not in d.columns or "docdb_family_id" not in d.columns:
            print(f"skipped second/{f.name}: needs docdb_family_id and {C.LABEL_COL}")
            continue
        d = d[pd.to_numeric(d[C.LABEL_COL], errors="coerce").isin([0, 1])]
        d = d[["docdb_family_id", C.LABEL_COL]].astype("int64").drop_duplicates("docdb_family_id", keep="last")
        d = d.rename(columns={C.LABEL_COL: f"lab_{f.stem}"})
        out = d if out is None else out.merge(d, on="docdb_family_id", how="outer")
    return out if out is not None else pd.DataFrame(columns=["docdb_family_id"])


def build_candidate_pool(patstat, db, labelled_relevant: set):
    family_id = TLS201_APPLN.docdb_family_id.label("docdb_family_id")
    kw_phrases = regex_to_like_phrases(C.SPECIFIC_KEYWORDS + C.GENERIC_KEYWORDS) + list(C.BLOCK6_SQL_PHRASES)

    pool_cpc = real_pi(db.query(family_id)
                       .join(TLS224_APPLN_CPC, TLS201_APPLN.appln_id == TLS224_APPLN_CPC.appln_id)
                       .filter(sql_prefix_any(TLS224_APPLN_CPC.cpc_class_symbol, C.BLOCK1_CPC + C.BLOCK4_CPC)))
    pool_title = real_pi(db.query(family_id)
                         .join(TLS202_APPLN_TITLE, TLS201_APPLN.appln_id == TLS202_APPLN_TITLE.appln_id)
                         .filter(sql_like_any(TLS202_APPLN_TITLE.appln_title, kw_phrases)))
    pool_abstract = real_pi(db.query(family_id)
                            .join(TLS203_APPLN_ABSTR, TLS201_APPLN.appln_id == TLS203_APPLN_ABSTR.appln_id)
                            .filter(sql_like_any(TLS203_APPLN_ABSTR.appln_abstract, kw_phrases)))
    title_low = func.lower(TLS202_APPLN_TITLE.appln_title)
    pool_b5 = real_pi(db.query(family_id)
                      .join(TLS202_APPLN_TITLE, TLS201_APPLN.appln_id == TLS202_APPLN_TITLE.appln_id)
                      .filter(or_(*(title_low.like(p) for p in C.BLOCK5_SQL_PATTERNS))))

    ids = pd.DataFrame(patstat.df(pool_cpc.union(pool_title, pool_abstract, pool_b5)))
    query_pool = set(ids["docdb_family_id"].dropna().astype("int64").drop_duplicates().tolist())
    pool_ids = sorted(query_pool | labelled_relevant)
    print(f"Candidate pool: {len(query_pool):,} families from the query + "
          f"{len(set(pool_ids) - query_pool):,} labelled relevant outside it | SQL phrases: {len(kw_phrases)}")
    return pool_ids, query_pool


def download_family_data(patstat, db, pool_ids):
    apps = fetch_by_family(
        patstat,
        lambda ids: real_pi(db.query(
            TLS201_APPLN.docdb_family_id, TLS201_APPLN.inpadoc_family_id, TLS201_APPLN.appln_id,
            TLS201_APPLN.appln_auth, TLS201_APPLN.appln_kind, TLS201_APPLN.appln_filing_date,
            TLS201_APPLN.appln_filing_year, TLS201_APPLN.earliest_filing_year, TLS201_APPLN.earliest_publn_year,
            TLS201_APPLN.granted, TLS201_APPLN.docdb_family_size, TLS201_APPLN.nb_citing_docdb_fam,
        ).filter(TLS201_APPLN.docdb_family_id.in_(ids))),
        pool_ids,
    )
    valid_year = apps["earliest_filing_year"].where(apps["earliest_filing_year"] != C.MISSING_YEAR)
    apps["family_year"] = valid_year.groupby(apps["docdb_family_id"]).transform("min")
    apps = apps[apps["family_year"].between(C.YEAR_MIN, C.YEAR_MAX)].copy()
    apps["family_year"] = apps["family_year"].astype(int)
    family_ids = apps["docdb_family_id"].drop_duplicates().tolist()
    print(f"Applications: {len(apps):,} | families with family year {C.YEAR_MIN}-{C.YEAR_MAX}: {len(family_ids):,}")

    def by_family(model, *cols):
        def make(ids):
            return real_pi(db.query(TLS201_APPLN.docdb_family_id, *cols)
                           .join(model, TLS201_APPLN.appln_id == model.appln_id)
                           .filter(TLS201_APPLN.docdb_family_id.in_(ids)))
        return fetch_by_family(patstat, make, family_ids)

    titles = by_family(TLS202_APPLN_TITLE, TLS202_APPLN_TITLE.appln_id, TLS202_APPLN_TITLE.appln_title,
                       TLS202_APPLN_TITLE.appln_title_lg)
    abstracts = by_family(TLS203_APPLN_ABSTR, TLS203_APPLN_ABSTR.appln_id, TLS203_APPLN_ABSTR.appln_abstract,
                          TLS203_APPLN_ABSTR.appln_abstract_lg)
    cpc = by_family(TLS224_APPLN_CPC, TLS224_APPLN_CPC.appln_id, TLS224_APPLN_CPC.cpc_class_symbol)
    cpc["cpc"] = cpc["cpc_class_symbol"].str.replace(" ", "", regex=False)
    ipc = by_family(TLS209_APPLN_IPC, TLS209_APPLN_IPC.appln_id, TLS209_APPLN_IPC.ipc_class_symbol)
    if ipc.empty:
        ipc = pd.DataFrame(columns=["docdb_family_id", "appln_id", "ipc_class_symbol", "ipc"])
    else:
        ipc["ipc"] = ipc["ipc_class_symbol"].str.replace(" ", "", regex=False)
    print(f"Titles: {len(titles):,} | abstracts: {len(abstracts):,} | CPC rows: {len(cpc):,} | IPC rows: {len(ipc):,}")
    return apps, family_ids, titles, abstracts, cpc, ipc


def build_units(apps, titles, abstracts, cpc, ipc):
    def join_unique(s):
        vals = [str(v).strip() for v in s.dropna().unique() if str(v).strip()]
        return " || ".join(vals)

    def code_lists(df, col, key):
        return df.groupby(key)[col].agg(lambda s: sorted(set(s)))

    def as_list(v):
        return v if isinstance(v, list) else []

    app_units = apps[["docdb_family_id", "appln_id"]].copy()
    app_units["titles"] = app_units["appln_id"].map(titles.set_index("appln_id")["appln_title"]).fillna("")
    app_units["abstracts"] = app_units["appln_id"].map(abstracts.set_index("appln_id")["appln_abstract"]).fillna("")
    app_units["cpc_codes"] = app_units["appln_id"].map(code_lists(cpc, "cpc", "appln_id")).apply(as_list)
    app_units["ipc_codes"] = app_units["appln_id"].map(code_lists(ipc, "ipc", "appln_id")).apply(as_list)

    fam = (apps.groupby("docdb_family_id")
           .agg(family_year=("family_year", "first"), n_applications=("appln_id", "nunique"),
                docdb_family_size=("docdb_family_size", "max"))
           .reset_index())
    first_app = apps.sort_values(["docdb_family_id", "appln_filing_date", "appln_id"]).drop_duplicates("docdb_family_id")
    fam["first_office"] = fam["docdb_family_id"].map(first_app.set_index("docdb_family_id")["appln_auth"])
    fam["titles"] = fam["docdb_family_id"].map(titles.groupby("docdb_family_id")["appln_title"].agg(join_unique)).fillna("")
    fam["abstracts"] = fam["docdb_family_id"].map(abstracts.groupby("docdb_family_id")["appln_abstract"].agg(join_unique)).fillna("")
    fam["cpc_codes"] = fam["docdb_family_id"].map(code_lists(cpc, "cpc", "docdb_family_id")).apply(as_list)
    fam["ipc_codes"] = fam["docdb_family_id"].map(code_lists(ipc, "ipc", "docdb_family_id")).apply(as_list)

    def lang(col):
        return col.fillna("").astype(str).str.strip().str.lower()

    def first_text(df, text_col, english_only):
        ok = df[text_col].fillna("").astype(str).str.strip().ne("")
        if english_only:
            ok &= lang(df[text_col + "_lg"]) == "en"
        return df[ok].drop_duplicates("docdb_family_id").set_index("docdb_family_id")[text_col]

    print("Title languages:", lang(titles["appln_title_lg"]).value_counts().head(5).to_dict())
    print("Abstract languages:", lang(abstracts["appln_abstract_lg"]).value_counts().head(5).to_dict())
    fam["abstract_en"] = fam["docdb_family_id"].map(first_text(abstracts, "appln_abstract", True))
    fam["title_en"] = (fam["docdb_family_id"].map(first_text(titles, "appln_title", True))
                       .fillna(fam["docdb_family_id"].map(first_text(titles, "appln_title", False))))
    fam["has_en_abstract"] = fam["abstract_en"].notna()
    print(f"Families with a title: {fam['title_en'].notna().mean():.1%}")
    print(f"Units: {len(app_units):,} applications, {len(fam):,} families | "
          f"with English abstract: {fam['has_en_abstract'].mean():.1%}")
    return app_units, fam


def select_sector(fam, app_units, labelled_relevant, labelled_not_relevant):
    flags = BLOCKS + ["has_cpc"] + EXCLUSIONS

    fam_level = apply_blocks(fam)
    app_level = flags_by_family(apply_blocks(app_units))
    app_level = summarise_blocks(app_level.reindex(fam["docdb_family_id"], fill_value=False)).reset_index()

    fam["in_sector_family"] = fam_level["in_sector"].values
    fam["in_sector_application"] = fam["docdb_family_id"].map(
        app_level.set_index("docdb_family_id")["in_sector"]).fillna(False)
    chosen = fam_level if C.MATCH_LEVEL == "family" else fam[["docdb_family_id"]].merge(
        app_level, on="docdb_family_id", how="left")
    for col in flags + ["matched", "excluded", "n_blocks", "block_combo"]:
        fam[col] = chosen[col].values
    fam["in_sector_rules"] = chosen["in_sector"].values
    fam = pd.concat([fam, grey_zone_flags(fam)], axis=1)
    fam["is_truncated"] = fam["family_year"] > C.LAST_COMPLETE_YEAR

    fam["corr_removed"] = C.USE_LABEL_CORRECTIONS & fam["in_sector_rules"] & fam["docdb_family_id"].isin(labelled_not_relevant)
    fam["corr_added"] = C.USE_LABEL_CORRECTIONS & ~fam["in_sector_rules"] & fam["docdb_family_id"].isin(labelled_relevant)
    fam["in_sector"] = (fam["in_sector_rules"] & ~fam["corr_removed"]) | fam["corr_added"]
    fam.loc[fam["corr_added"], "block_combo"] = "added by label"

    sector = fam[fam["in_sector"]].copy()
    rule_sector = fam[fam["in_sector_rules"]].copy()
    gz = [c for c in fam.columns if c.startswith("gz_")]

    print(f"Families selected by the blocks ({C.MATCH_LEVEL} level): {fam['matched'].sum():,} (out of {len(fam):,} candidates)")
    print(f"  removed by the grey-zone exclusion rules: {(fam['matched'] & fam['excluded']).sum():,}")
    print(f"  = sector given by the rules: {len(rule_sector):,} | application level: "
          f"{fam['in_sector_application'].sum():,} | family level: {fam['in_sector_family'].sum():,}")
    print(f"  hand-labelled corrections: -{fam['corr_removed'].sum():,} labelled not relevant, "
          f"+{fam['corr_added'].sum():,} labelled relevant outside the rules")
    print(f"SECTOR: {len(sector):,} families | truncated (> {C.LAST_COMPLETE_YEAR}): {sector['is_truncated'].sum():,}")
    return fam, sector, rule_sector, flags, gz


def report_grey_zones_and_coverage(fam, sector, gz, flags):
    out = C.OUT_STEP0
    show(pd.DataFrame({"sector families": sector[gz].sum(), "share of sector (%)": sector[gz].mean() * 100}),
         "4.1 Grey-zone flags in the final sector", 1)
    excl = fam[fam["matched"] & fam["excluded"]]
    print(f"Families removed by the exclusion rules: {len(excl)}")
    excl[["docdb_family_id", "title_en"] + [c for c in EXCLUSIONS if excl[c].any()]].to_csv(
        out / "grey_zone_excluded.csv", index=False)

    cov = pd.DataFrame({
        "candidates": fam.groupby("first_office").size(),
        "candidates without CPC": fam[~fam["has_cpc"]].groupby("first_office").size(),
        "sector families": sector.groupby("first_office").size(),
        "sector families without CPC (keywords only)": sector[~sector["has_cpc"]].groupby("first_office").size(),
    }).fillna(0).astype(int).sort_values("candidates", ascending=False)
    cov = pd.concat([cov.head(12), cov.iloc[12:].sum().to_frame("other offices").T, cov.sum().to_frame("total").T])
    cov["% candidates without CPC"] = (cov["candidates without CPC"] / cov["candidates"].replace(0, np.nan) * 100).round(1)
    show(cov, "4.2 CPC coverage by first office", 1)
    cov.to_csv(out / "cpc_coverage.csv")


def block_overlap(rule_sector):
    combo_counts = rule_sector["block_combo"].value_counts()
    alone = {b: int((rule_sector[b] & (rule_sector["n_blocks"] == 1)).sum()) for b in BLOCKS}
    total = {b: int(rule_sector[b].sum()) for b in BLOCKS}
    tab = pd.DataFrame({"families with the block": total, "only this block": alone})
    show(tab, "5 Block overlap")
    combo_counts.to_frame("families").to_csv(C.OUT_STEP0 / "block_combinations.csv")
    tab.to_csv(C.OUT_STEP0 / "block_overlap.csv")

    fig, ax = plt.subplots(figsize=(8, 0.4 * len(combo_counts) + 1))
    combo_counts.sort_values().plot.barh(ax=ax, color="#2a78d6")
    ax.set_xlabel("families")
    ax.set_title(f"Which blocks select each family ({C.MATCH_LEVEL} level, {C.ENV})")
    plt.tight_layout()
    fig.savefig(C.OUT_STEP0 / "block_overlap.png", dpi=150)
    plt.close(fig)


def broad_net(patstat, db, rule_sector):
    family_id = TLS201_APPLN.docdb_family_id.label("docdb_family_id")
    b_cpc = real_pi(db.query(family_id).join(TLS224_APPLN_CPC, TLS201_APPLN.appln_id == TLS224_APPLN_CPC.appln_id)
                    .filter(sql_prefix_any(TLS224_APPLN_CPC.cpc_class_symbol, C.BROAD_CPC)))
    b_ipc = real_pi(db.query(family_id).join(TLS209_APPLN_IPC, TLS201_APPLN.appln_id == TLS209_APPLN_IPC.appln_id)
                    .filter(sql_prefix_any(TLS209_APPLN_IPC.ipc_class_symbol, C.BROAD_IPC)))
    b_title = real_pi(db.query(family_id).join(TLS202_APPLN_TITLE, TLS201_APPLN.appln_id == TLS202_APPLN_TITLE.appln_id)
                      .filter(sql_like_any(TLS202_APPLN_TITLE.appln_title, C.BROAD_KEYWORDS)))
    b_abs = real_pi(db.query(family_id).join(TLS203_APPLN_ABSTR, TLS201_APPLN.appln_id == TLS203_APPLN_ABSTR.appln_id)
                    .filter(sql_like_any(TLS203_APPLN_ABSTR.appln_abstract, C.BROAD_KEYWORDS)))
    broad_ids = pd.DataFrame(patstat.df(b_cpc.union(b_ipc, b_title, b_abs)))["docdb_family_id"].astype("int64").unique()
    broad_years = fetch_by_family(
        patstat,
        lambda ids: real_pi(db.query(TLS201_APPLN.docdb_family_id,
                                     func.min(TLS201_APPLN.earliest_filing_year).label("family_year"))
                            .filter(TLS201_APPLN.docdb_family_id.in_(ids))
                            .filter(TLS201_APPLN.earliest_filing_year != C.MISSING_YEAR))
        .group_by(TLS201_APPLN.docdb_family_id),
        broad_ids,
    )
    broad_set = set(broad_years.loc[broad_years["family_year"].between(C.YEAR_MIN, C.YEAR_MAX), "docdb_family_id"])
    rule_ids = set(rule_sector["docdb_family_id"])
    broad_outside = broad_set - rule_ids
    print(f"Broad net: {len(broad_set):,} families | in the sector: {len(broad_set & rule_ids):,} "
          f"| outside the sector: {len(broad_outside):,}")
    print(f"Sector families the broad net does not find: {len(rule_ids - broad_set):,} "
          "(the blocks reach patents a generic query misses)")
    return broad_set, broad_outside, broad_years


def write_label_samples(patstat, db, fam, rule_sector, labels, broad_outside, broad_years, gz):
    label_columns = ["docdb_family_id", "sample", "family_year", "first_office", "title_en", "abstract_en", "titles",
                     C.LABEL_COL, C.LABEL_COL_BROAD, "grey_zone", "note"]
    key_columns = ["docdb_family_id", "block_combo", "in_sector_rules", "excluded"] + EXCLUSIONS + gz
    already = set(labels["docdb_family_id"])
    keys = []

    def to_label_file(df, name, sample, n=None):
        if df.empty:
            print(f"{name}: no family to label")
            return
        out = df.sample(n=min(n, len(df)), random_state=C.SEED) if n else df.copy()
        out["sample"] = sample
        for c in [C.LABEL_COL, C.LABEL_COL_BROAD, "grey_zone", "note"]:
            out[c] = ""
        out[[c for c in label_columns if c in out.columns]].to_csv(C.OUT_STEP0 / name, index=False)
        keys.append(out[[c for c in key_columns if c in out.columns]].assign(file=name))
        print(f"{name}: {len(out)} families")

    to_label_file(rule_sector[~rule_sector["docdb_family_id"].isin(already)], "label_sample_sector.csv", "sector",
                  C.SAMPLE_SIZE)
    b6_only = rule_sector[rule_sector["b6_vehicle_task"] & (rule_sector["n_blocks"] == 1)]
    to_label_file(b6_only[~b6_only["docdb_family_id"].isin(already)], "label_sample_block6.csv", "block6",
                  C.BLOCK6_SAMPLE_SIZE)

    pending = sorted(broad_outside - already)
    if pending:
        sample_ids = pd.Series(pending).sample(n=min(C.BROAD_SAMPLE_SIZE, len(pending)),
                                               random_state=C.SEED).tolist()
        broad_fam = fam[fam["docdb_family_id"].isin(sample_ids)]
        missing = set(sample_ids) - set(broad_fam["docdb_family_id"])
        if missing:
            t = fetch_by_family(patstat, lambda ids: real_pi(
                db.query(TLS201_APPLN.docdb_family_id, TLS202_APPLN_TITLE.appln_title)
                .join(TLS202_APPLN_TITLE, TLS201_APPLN.appln_id == TLS202_APPLN_TITLE.appln_id)
                .filter(TLS201_APPLN.docdb_family_id.in_(ids))), missing)
            a = fetch_by_family(patstat, lambda ids: real_pi(
                db.query(TLS201_APPLN.docdb_family_id, TLS203_APPLN_ABSTR.appln_abstract)
                .join(TLS203_APPLN_ABSTR, TLS201_APPLN.appln_id == TLS203_APPLN_ABSTR.appln_id)
                .filter(TLS201_APPLN.docdb_family_id.in_(ids))), missing)
            extra = pd.DataFrame({"docdb_family_id": sorted(missing)})
            if not t.empty:
                extra["title_en"] = extra["docdb_family_id"].map(
                    t.drop_duplicates("docdb_family_id").set_index("docdb_family_id")["appln_title"])
            if not a.empty:
                extra["abstract_en"] = extra["docdb_family_id"].map(
                    a.drop_duplicates("docdb_family_id").set_index("docdb_family_id")["appln_abstract"])
            extra = extra.merge(broad_years, on="docdb_family_id", how="left").assign(block_combo="outside candidate pool")
            broad_fam = pd.concat([broad_fam, extra], ignore_index=True)
        to_label_file(broad_fam, "label_sample_broad_net.csv", "broad_net_outside")
    else:
        print("label_sample_broad_net.csv: no family to label")

    if keys:
        pd.concat(keys, ignore_index=True).to_csv(C.OUT_STEP0 / "label_samples_key.csv", index=False)


def stratum_of(df):
    return np.where(df["b6_vehicle_task"] & (df["n_blocks"] == 1), "block 6 only", "blocks 1-5")


def stratified_precision(rule_sector, lab, col):
    sizes = rule_sector["stratum"].value_counts()
    rows, covered = [], 0
    for h, n_h_total in sizes.items():
        d = lab[lab["stratum"] == h]
        k_h, n_h = int(d[col].sum()), len(d)
        lo_h, hi_h = wilson(k_h, n_h) if n_h else (np.nan, np.nan)
        rows.append({"stratum": h, "families": n_h_total, "labelled": n_h, "relevant": k_h,
                     "precision": k_h / n_h if n_h else np.nan, "ci_low": lo_h, "ci_high": hi_h})
        covered += n_h_total if n_h else 0
    t = pd.DataFrame(rows).set_index("stratum")
    ok = t[t["labelled"] > 0]
    w = ok["families"] / ok["families"].sum()
    p = float((w * ok["precision"]).sum())
    ci = (float((w * ok["ci_low"]).sum()), float((w * ok["ci_high"]).sum()))
    return p, ci, t, covered / len(rule_sector)


def validate_precision(fam, rule_sector, labels, gz):
    out = C.OUT_STEP0
    rule_sector = rule_sector.assign(stratum=stratum_of(rule_sector))
    lab_all = rule_sector.merge(
        labels.drop(columns=[c for c in ["family_year", "first_office", "title_en", "abstract_en", "titles"]
                             if c in labels]),
        on="docdb_family_id", how="inner")
    lab = lab_all[lab_all["sample"].isin(C.PRECISION_SAMPLES)
                  & ~lab_all["label_file"].isin(C.VALIDATION_EXCLUDE_FILES)].copy()
    print(f"\nLabelled families in the rule sector: {len(lab_all)} | used for precision (random PROD samples): {len(lab)}")

    res = {"rule_sector": rule_sector, "lab": lab, "P": np.nan, "P_CI": (np.nan, np.nan), "P_broad": np.nan,
           "P_CI_broad": (np.nan, np.nan), "strata": None}
    if lab.empty:
        print("No PROD labels for the sector yet: fill label_sample_sector.csv / label_sample_block6.csv "
              "and save them in labels/.")
        return res

    p, ci, strata_table, coverage = stratified_precision(rule_sector, lab, C.LABEL_COL)
    p_b, ci_b, _, _ = stratified_precision(rule_sector, lab, C.LABEL_COL_BROAD)
    res.update(P=p, P_CI=ci, P_broad=p_b, P_CI_broad=ci_b, strata=strata_table)
    print(f"7.1 Precision of the rule sector (strict rule, stratified): {p:.1%} "
          f"(95% CI {ci[0]:.1%}-{ci[1]:.1%}) on {len(lab)} labelled families out of {len(rule_sector):,}")
    print(f"    broad rule (upper bound): {p_b:.1%} (95% CI {ci_b[0]:.1%}-{ci_b[1]:.1%})")
    show(strata_table)
    strata_table.round(4).to_csv(out / "precision_strata.csv")
    if coverage < 1:
        print(f"WARNING: strata without labels ({1 - coverage:.0%} of the rule sector): the precision above covers "
              "only the labelled strata. Label label_sample_block6.csv.")

    by_combo = precision_table(lab, "block_combo")
    show(by_combo, "Precision by block combination")
    by_combo.to_csv(out / "precision_by_block_combo.csv")
    by_block = pd.DataFrame({b: precision_table(lab[lab[b]], lambda _: b).iloc[0] for b in BLOCKS if lab[b].any()}).T
    show(by_block, "Precision of each block (alone or with others)")
    by_block.to_csv(out / "precision_by_block.csv")

    lab["period"] = pd.cut(lab["family_year"], bins=[1999, 2009, 2016, C.LAST_COMPLETE_YEAR, C.YEAR_MAX],
                           labels=["2000-09", "2010-16", f"2017-{C.LAST_COMPLETE_YEAR % 100}", "truncated"])
    by_period = precision_table(lab, "period")
    show(by_period, "Precision by period (if it changes, growth rates in Step 1 are biased)")
    by_period.to_csv(out / "precision_by_period.csv")
    lab["office"] = lab["first_office"].where(lab["first_office"].isin(["CN", "US", "EP", "JP", "KR", "WO"]), "other")
    by_office = precision_table(lab, "office")
    show(by_office, "Precision by first filing office (if it changes, geography in Step 1 is biased)")
    by_office.to_csv(out / "precision_by_office.csv")

    excl_lab = fam[fam["matched"] & fam["excluded"]].merge(labels[["docdb_family_id", C.LABEL_COL]], on="docdb_family_id")
    matched_lab = fam[fam["matched"]].merge(labels[["docdb_family_id", C.LABEL_COL, "sample", "label_file"]],
                                            on="docdb_family_id")
    matched_lab = matched_lab[matched_lab["sample"].isin(C.PRECISION_SAMPLES)
                              & ~matched_lab["label_file"].isin(C.VALIDATION_EXCLUDE_FILES)]
    if not excl_lab.empty:
        print(f"\nLabelled families removed by the exclusion rules: {len(excl_lab)}, "
              f"of which relevant: {int(excl_lab[C.LABEL_COL].sum())}")
        rows = {}
        for name, d in {"blocks only": matched_lab,
                        "blocks minus exclusions (the rule sector)": matched_lab[~matched_lab["excluded"]]}.items():
            kk, nn = int(d[C.LABEL_COL].sum()), len(d)
            rows[name] = {"labelled": nn, "relevant": kk, "precision": kk / nn if nn else np.nan,
                          "ci_low": wilson(kk, nn)[0], "ci_high": wilson(kk, nn)[1]}
        excl_tab = pd.DataFrame(rows).T
        show(excl_tab, "Effect of the exclusion rules")
        excl_tab.round(4).to_csv(out / "precision_exclusions.csv")

    gz_lab = lab["grey_zone"].fillna("").astype(str).str.strip().ne("")
    source = "the labellers' grey_zone column"
    if not gz_lab.any():
        gz_lab, source = lab[gz].any(axis=1), "the automatic topic flags (grey_zone is still empty)"
    sens = {}
    for name, y in {"(a) as labelled": lab[C.LABEL_COL],
                    "(b) grey zones = relevant": lab[C.LABEL_COL].where(~gz_lab, 1),
                    "(c) grey zones = not relevant": lab[C.LABEL_COL].where(~gz_lab, 0),
                    "(d) grey zones left out": lab.loc[~gz_lab, C.LABEL_COL],
                    "(e) broad rule (is_relevant_broad)": lab[C.LABEL_COL_BROAD]}.items():
        kk, nn = int(y.sum()), len(y)
        sens[name] = {"families": nn, "precision": kk / nn if nn else np.nan,
                      "ci_low": wilson(kk, nn)[0], "ci_high": wilson(kk, nn)[1]}
    sens_tab = pd.DataFrame(sens).T
    show(sens_tab, f"Grey-zone sensitivity: {int(gz_lab.sum())} labelled sector families in a grey zone, from {source}")
    sens_tab.round(4).to_csv(out / "precision_grey_zone_sensitivity.csv")

    fp = lab[lab[C.LABEL_COL] == 0]
    print(f"\n7.5 {len(fp)} sector families labelled not relevant (see false_positives.csv)")
    fp[["docdb_family_id", "title_en", "block_combo"] + [c for c in ["grey_zone", "note"] if c in fp]].to_csv(
        out / "false_positives.csv", index=False)
    show(fp["block_combo"].value_counts().to_frame("false positives"))
    return res


def agreement_between_labellers(fam, labels, second_labels):
    from sklearn.metrics import cohen_kappa_score

    agree = labels[["docdb_family_id", C.LABEL_COL, "label_file", "sample"]].rename(columns={C.LABEL_COL: "lab_main"})
    if "label_b" in labels and pd.to_numeric(labels["label_b"], errors="coerce").notna().any():
        agree["lab_label_b"] = pd.to_numeric(labels["label_b"], errors="coerce")
    if not second_labels.empty and second_labels.shape[1] > 1:
        agree = agree.merge(second_labels, on="docdb_family_id", how="outer")
    raters = [c for c in agree.columns if c.startswith("lab_")]
    rows, disagreements = [], []
    for a, b in combinations(raters, 2):
        d = agree[["docdb_family_id", a, b]].dropna()
        if len(d) < 2:
            continue
        kappa = cohen_kappa_score(d[a], d[b]) if d[a].nunique() > 1 or d[b].nunique() > 1 else np.nan
        rows.append({"pair": f"{a[4:]} vs {b[4:]}", "families": len(d), "agreement": (d[a] == d[b]).mean(),
                     "kappa": kappa, f"1 for {a[4:]} only": int(((d[a] == 1) & (d[b] == 0)).sum()),
                     f"1 for {b[4:]} only": int(((d[a] == 0) & (d[b] == 1)).sum())})
        disagreements.append(d[d[a] != d[b]].assign(pair=f"{a[4:]} vs {b[4:]}"))
    kappa_tab = pd.DataFrame(rows).set_index("pair") if rows else pd.DataFrame()
    if kappa_tab.empty:
        print("No second labelling yet (labels/second/ or label_b): kappa not computed.")
        return kappa_tab
    show(kappa_tab, "7.2 Agreement between labellers")
    kappa_tab.round(4).to_csv(C.OUT_STEP0 / "label_agreement.csv")
    dis = pd.concat(disagreements, ignore_index=True)
    if not dis.empty:
        dis = dis.merge(fam[["docdb_family_id", "title_en"]], on="docdb_family_id", how="left")
        print(f"{dis['docdb_family_id'].nunique()} families with a disagreement: adjudicate them "
              "(label_disagreements.csv), write the agreed label in the main file and re-run")
        dis.to_csv(C.OUT_STEP0 / "label_disagreements.csv", index=False)
    return kappa_tab


def validate_recall(val, labels, broad_outside):
    rule_sector, lab = val["rule_sector"], val["lab"]
    broad_lab = labels[labels["docdb_family_id"].isin(broad_outside) & (labels["sample"] == "broad_net_outside")
                       & ~labels["label_file"].isin(C.VALIDATION_EXCLUDE_FILES)]
    recall, recall_range = np.nan, (np.nan, np.nan)
    if lab.empty or broad_lab.empty:
        print("Needs PROD labels for the sector and for the broad-net sample (label_sample_broad_net.csv).")
        return broad_lab, recall, recall_range
    kb, nb = int(broad_lab[C.LABEL_COL].sum()), len(broad_lab)
    p_miss = kb / nb
    lo_b, hi_b = wilson(kb, nb)
    kept, missed = val["P"] * len(rule_sector), p_miss * len(broad_outside)
    recall = kept / (kept + missed)
    ci = val["P_CI"]
    worst = ci[0] * len(rule_sector) / (ci[0] * len(rule_sector) + hi_b * len(broad_outside))
    best = ci[1] * len(rule_sector) / (ci[1] * len(rule_sector) + lo_b * len(broad_outside))
    recall_range = (worst, best)
    print(f"\n7.4 Broad-net families outside the sector: {len(broad_outside):,}; relevant among the {nb} labelled: "
          f"{kb} ({p_miss:.1%}, Wilson {lo_b:.1%}-{hi_b:.1%})")
    print(f"Estimated relevant families: in the sector {kept:,.0f} | missed {missed:,.0f} "
          f"(range {lo_b * len(broad_outside):,.0f}-{hi_b * len(broad_outside):,.0f}) | recall {recall:.1%} "
          f"(range {worst:.1%}-{best:.1%})")
    return broad_lab, recall, recall_range


def save_corrections(fam, rule_sector, sector):
    corr = fam[fam["corr_removed"] | fam["corr_added"]].assign(
        correction=lambda d: np.where(d["corr_added"], "added (labelled 1)", "removed (labelled 0)"))
    print(f"\n7.6 Rule sector {len(rule_sector):,} - {fam['corr_removed'].sum():,} + {fam['corr_added'].sum():,} "
          f"= final sector {len(sector):,} families")
    corr[["docdb_family_id", "correction"]].to_csv(C.OUT_STEP0 / "label_corrections.csv", index=False)


def save_outputs(fam, sector, flags, gz, apps, titles, abstracts, cpc, ipc):
    out = C.OUT_STEP0
    flag_cols = (["docdb_family_id", "family_year", "is_truncated", "first_office", "has_en_abstract"] + flags
                 + ["matched", "excluded", "in_sector_application", "in_sector_family", "in_sector_rules",
                    "corr_removed", "corr_added", "n_blocks", "block_combo", "in_sector"] + gz)
    sector[flag_cols].to_csv(out / "sector_families.csv", index=False)
    fam[flag_cols].to_csv(out / "candidates_all.csv", index=False)

    ids = set(sector["docdb_family_id"])
    raw = out / "raw"
    apps[apps["docdb_family_id"].isin(ids)].to_csv(raw / "applications.csv.gz", index=False)
    titles[titles["docdb_family_id"].isin(ids)].to_csv(raw / "titles.csv.gz", index=False)
    abstracts[abstracts["docdb_family_id"].isin(ids)].to_csv(raw / "abstracts.csv.gz", index=False)
    cpc[cpc["docdb_family_id"].isin(ids)][["docdb_family_id", "appln_id", "cpc"]].to_csv(
        raw / "cpc.csv.gz", index=False)
    if not ipc.empty:
        ipc[ipc["docdb_family_id"].isin(ids)][["docdb_family_id", "appln_id", "ipc"]].to_csv(
            raw / "ipc.csv.gz", index=False)
    print(f"\nSaved in {out.resolve()}: {len(sector):,} sector families")


def fmt_ci(p, ci):
    return f"{p:.0%} ({ci[0]:.0%}-{ci[1]:.0%})" if p == p else "n/a"


def summary_table(fam, sector, rule_sector, val, kappa, recall, recall_range, broad_lab, labels):
    def pct(k, n):
        lo, hi = wilson(k, n)
        return f"{k / n:.0%} ({lo:.0%}-{hi:.0%}, {k}/{n})" if n else "n/a"

    lab, strata = val["lab"], val["strata"]
    s = {"environment": C.ENV, "IPR type": C.IPR_TYPE, "matching level": C.MATCH_LEVEL,
         "families selected by the blocks": int(fam["matched"].sum()),
         "removed by the grey-zone exclusion rules": int((fam["matched"] & fam["excluded"]).sum()),
         "sector given by the rules": len(rule_sector),
         "hand-labelled corrections (removed / added)": f"{int(fam['corr_removed'].sum())} / {int(fam['corr_added'].sum())}",
         "FINAL SECTOR (families)": len(sector),
         "of which truncated": int(sector["is_truncated"].sum()),
         "labels used for validation": f"random PROD samples ({len(lab)} sector families, {len(broad_lab)} broad-net)"}
    if not lab.empty:
        s["precision of the rule sector (strict rule, stratified)"] = fmt_ci(val["P"], val["P_CI"])
        s["precision of the rule sector (broad rule, upper bound)"] = fmt_ci(val["P_broad"], val["P_CI_broad"])
        for h, r in strata.iterrows():
            s[f"precision, stratum {h}"] = pct(int(r["relevant"]), int(r["labelled"])) if r["labelled"] else "NOT LABELLED"
    if not np.isnan(recall):
        s["recall relative to the broad net"] = fmt_ci(recall, recall_range)
    if not kappa.empty:
        for pair, r in kappa.iterrows():
            s[f"kappa {pair}"] = f"{r['kappa']:.2f} on {int(r['families'])} families ({r['agreement']:.0%} agreement)"
    checks = {
        "labels found in labels/": not labels.empty,
        "broad-net sample labelled": not broad_lab.empty,
        "discovery sample labelled": bool((labels["sample"] == "discovery").any()) if not labels.empty else False,
        "every stratum of the sector labelled": (not lab.empty) and bool((strata["labelled"] > 0).all()),
        "second labelling (kappa)": not kappa.empty,
        "grey_zone column filled": labels["grey_zone"].ne("").any() if not labels.empty else False,
        "run on PROD": C.ENV == "PROD",
    }
    s.update({f"check: {k}": ("yes" if v else "NO") for k, v in checks.items()})
    summary_df = pd.Series(s, name="value").to_frame()
    show(summary_df, "10 Summary")
    summary_df.to_csv(C.OUT_STEP0 / "step0_summary.csv")
    if not all(checks.values()):
        print("Provisional: " + ", ".join(k for k, v in checks.items() if not v) + ".")


def draw_prisma(n, path=None):
    BLUE, INK, SUB, LINE = "#2a78d6", "#0b0b0b", "#52514e", "#a3a29c"
    FILL_MAIN, FILL_OUT, FILL_VAL = "#eaf2fc", "#f4f3f0", "#fff6e0"
    fig, ax = plt.subplots(figsize=(15.5, 9.2))
    ax.set_xlim(0, 15.5); ax.set_ylim(0.9, 10.1); ax.axis("off")

    def box(x, y, w, h, title, body, fill, edge):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                    fc=fill, ec=edge, lw=1.3))
        ax.text(x + 0.18, y + h - 0.2, title, ha="left", va="top", fontsize=11.5, fontweight="bold", color=INK)
        ax.text(x + 0.18, y + h - 0.58, body, ha="left", va="top", fontsize=9.3, color=SUB, linespacing=1.45)

    def arrow(x0, y0, x1, y1):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0), arrowprops=dict(arrowstyle="-|>", color=LINE, lw=1.4))

    X, W, H, XO, WO, HO = 0.2, 7.4, 1.3, 8.2, 3.6, 1.6
    ys = [8.6, 6.75, 4.9, 3.05, 1.2]
    main = [
        (f"Candidate pool - {n['pool']:,} families",
         f"SQL on PATSTAT {n['env']}: CPC B63G2008/..., B63G8/, B63C11/ - keyword phrases in titles\n"
         f"and abstracts - ROV/AUV/UUV in titles - + {n['pool_labelled']:,} labelled relevant outside the query"),
        (f"Candidates in the window - {n['window']:,} families",
         f"patents of invention (PI), family year (earliest priority) {n['years']}\n"
         f"titles, abstracts and CPC codes pooled at family level"),
        (f"Selected by the blocks - {n['matched']:,} families",
         "at least one of the 6 blocks: CPC + underwater term - specific keywords - generic\n"
         "keywords - neighbouring codes + robot term - acronym in title - vehicle + task"),
        (f"Sector given by the rules - {n['rule_sector']:,} families",
         f"validated on random PROD samples (Section 7)\n"
         f"precision {n['precision']} - recall vs broad net {n['recall']}"),
        (f"FINAL SECTOR - {n['final']:,} families",
         f"of which {n['truncated']:,} in the truncated years {n['trunc_years']} (shaded, not a decline)\n"
         f"input of 02_clean and of every step of the deck"),
    ]
    outs = [
        ("Outside the window", f"{n['out_window']:,} families\npriority outside {n['years']}\nor no valid PI application"),
        ("Not selected", f"{n['not_matched']:,} families\nmatched no block"),
        ("Grey-zone exclusion", f"{n['excluded']:,} families\nenergy device / offshore structure\nwithout a vehicle term"),
        ("Hand-labelled corrections",
         f"- {n['corr_removed']:,} labelled not relevant\n+ {n['corr_added']:,} labelled relevant\n(broad net, discovery)"),
    ]
    for i, (y, (t, b)) in enumerate(zip(ys, main)):
        last = i == len(main) - 1
        box(X, y, W, H, t, b, BLUE if last else FILL_MAIN, BLUE)
        if last:
            for txt in ax.texts[-2:]:
                txt.set_color("white")
        if i < len(main) - 1:
            arrow(X + W / 2, y, X + W / 2, ys[i + 1] + H)
    for i, (t, b) in enumerate(outs):
        y_mid = (ys[i] + ys[i + 1] + H) / 2
        box(XO, y_mid - HO / 2, WO, HO, t, b, FILL_OUT, LINE)
        arrow(X + W / 2, y_mid, XO, y_mid)

    box(12.25, 1.2, 3.1, 8.7, "Validation", n["validation"], FILL_VAL, "#eda100")
    ax.set_title(f"{n['sector_name']}: selection of the patent families (PATSTAT {n['env']})", loc="left",
                 fontsize=13, color=INK, pad=6)
    fig.text(0.01, 0.02, "Flow in the style of the PRISMA 2020 statement (Page et al., 2021); counts are DOCDB families.",
             fontsize=8.5, color=SUB)
    if path:
        fig.savefig(path, dpi=200, bbox_inches="tight")
    return fig


def prisma(fam, sector, rule_sector, pool_ids, query_pool, val, kappa, recall, recall_range, broad_lab):
    lab, strata = val["lab"], val["strata"]
    val_lines = [f"Precision (strict rule)\n{fmt_ci(val['P'], val['P_CI'])}", f"on {len(lab)} random families"]
    if not lab.empty:
        for h, r in strata.iterrows():
            val_lines.append(f"- {h}: {int(r['relevant'])}/{int(r['labelled'])}" if r["labelled"] else f"- {h}: not labelled")
    val_lines += ["", f"Recall vs broad net\n{fmt_ci(recall, recall_range)}", f"broad-net sample: {len(broad_lab)}", ""]
    if not kappa.empty:
        val_lines.append("Agreement (Cohen's kappa)")
        val_lines += [f"- {pair}: {r['kappa']:.2f}" for pair, r in kappa.iterrows()]
    else:
        val_lines.append("Agreement: not yet measured")
    counts = {
        "sector_name": C.SECTOR_NAME, "env": C.ENV, "years": f"{C.YEAR_MIN}-{C.YEAR_MAX}",
        "pool": len(query_pool), "pool_labelled": len(set(pool_ids) - query_pool),
        "window": len(fam), "out_window": len(pool_ids) - len(fam),
        "matched": int(fam["matched"].sum()), "not_matched": int((~fam["matched"]).sum()),
        "excluded": int((fam["matched"] & fam["excluded"]).sum()),
        "rule_sector": len(rule_sector),
        "corr_removed": int(fam["corr_removed"].sum()), "corr_added": int(fam["corr_added"].sum()),
        "final": len(sector), "truncated": int(sector["is_truncated"].sum()),
        "trunc_years": f"{C.LAST_COMPLETE_YEAR + 1}-{C.YEAR_MAX}",
        "precision": fmt_ci(val["P"], val["P_CI"]), "recall": fmt_ci(recall, recall_range),
        "validation": "\n".join(val_lines),
    }
    assert counts["matched"] - counts["excluded"] == counts["rule_sector"], "flow does not add up (exclusions)"
    assert counts["rule_sector"] - counts["corr_removed"] + counts["corr_added"] == counts["final"], \
        "flow does not add up (corrections)"
    fig = draw_prisma(counts, C.OUT_STEP0 / "prisma_flow.png")
    plt.close(fig)
    pd.Series({k: v for k, v in counts.items() if k != "validation"}, name="value").to_csv(
        C.OUT_STEP0 / "prisma_counts.csv")


def run_retrieval(patstat, db, fam, rule_sector, val, labels, broad_outside, broad_years, use_sbert=True):
    from scipy.stats import binomtest
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer, TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    from pw_utils import BM25, PATENT_STOP_WORDS, tokenize

    out = C.OUT_STEP0
    lab = val["lab"]
    rx_specific, rx_generic = compile_any(C.SPECIFIC_KEYWORDS), compile_any(C.GENERIC_KEYWORDS)
    focal_ids = list(C.FOCAL_FAMILY_IDS)
    description = C.SECTOR_DESCRIPTION
    cache = {}

    def embed(texts):
        if "model" not in cache:
            from sentence_transformers import SentenceTransformer
            cache["model"] = SentenceTransformer(C.SBERT_MODEL)
        return cache["model"].encode(list(texts), batch_size=32, normalize_embeddings=True,
                                     show_progress_bar=True, convert_to_numpy=True)

    def mcnemar(truth, a, b):
        ca, cb = (a == truth), (b == truth)
        n01, n10 = int((ca & ~cb).sum()), int((~ca & cb).sum())
        return binomtest(min(n01, n10), n01 + n10, 0.5).pvalue if n01 + n10 else 1.0

    def top_k(scores, k):
        sel = np.zeros(len(scores), dtype=bool)
        sel[np.argsort(-scores, kind="stable")[:k]] = True
        return sel

    def evaluate(name, sel, truth):
        tp = int((sel & truth).sum())
        p = tp / sel.sum() if sel.sum() else np.nan
        r = tp / truth.sum() if truth.sum() else np.nan
        lo, hi = wilson(tp, int(sel.sum()))
        return {"method": name, "selected": int(sel.sum()), "precision": p, "ci_low": lo, "ci_high": hi,
                "recall": r, "F1": 2 * p * r / (p + r) if p + r else np.nan}

    if not lab.empty:
        focal_df = fetch_by_family(patstat, lambda ids: real_pi(
            db.query(TLS201_APPLN.docdb_family_id, TLS203_APPLN_ABSTR.appln_abstract)
            .join(TLS203_APPLN_ABSTR, TLS201_APPLN.appln_id == TLS203_APPLN_ABSTR.appln_id)
            .filter(TLS203_APPLN_ABSTR.appln_abstract_lg == "en")
            .filter(TLS201_APPLN.docdb_family_id.in_(ids))), focal_ids)
        focal_df = focal_df.drop_duplicates("docdb_family_id") if not focal_df.empty else focal_df
        print(f"\n8.1 {len(focal_df)} of {len(focal_ids)} focal patents found in {C.ENV}")

        ev = lab[lab["has_en_abstract"] & ~lab["docdb_family_id"].isin(focal_ids)].reset_index(drop=True)
        truth = ev[C.LABEL_COL].astype(bool).to_numpy()
        texts = ev["abstract_en"].fillna("").tolist()
        focal_texts = focal_df["appln_abstract"].fillna("").tolist() if len(focal_df) else []
        full = (ev["title_en"].fillna("") + " " + ev["abstract_en"].fillna("")).str.lower()
        by_kw = (full.str.contains(rx_specific) | full.str.contains(rx_generic)).to_numpy()
        k = int(by_kw.sum())
        print(f"Evaluation set: {len(ev)} families, {truth.sum()} relevant | k = {k}")

        bm25 = BM25([tokenize(t) for t in texts])
        sel = {"A - keywords": by_kw, "C - description BM25": top_k(bm25.scores(tokenize(description)), k)}
        if focal_texts:
            s_bm25_focal = np.max([bm25.scores(tokenize(t)) for t in focal_texts], axis=0)
            tfidf = TfidfVectorizer(stop_words=list(ENGLISH_STOP_WORDS | PATENT_STOP_WORDS), ngram_range=(1, 2),
                                    min_df=2, max_df=0.5, sublinear_tf=True).fit_transform(texts + focal_texts)
            s_tfidf = cosine_similarity(tfidf[:len(texts)], tfidf[len(texts):]).max(axis=1)
            sel["B - focal BM25"] = top_k(s_bm25_focal, k)
            sel["B - focal TF-IDF"] = top_k(s_tfidf, k)
        if use_sbert:
            emb = embed(texts)
            desc_vec = embed([description])[0]
            sel["C - description Patent SBERT"] = top_k(emb @ desc_vec, k)
            if focal_texts:
                emb_focal = embed(focal_texts)
                sel["B - focal Patent SBERT"] = top_k((emb @ emb_focal.T).max(axis=1), k)
                sel["A or B (SBERT)"] = sel["A - keywords"] | sel["B - focal Patent SBERT"]
                sel["A and B (SBERT)"] = sel["A - keywords"] & sel["B - focal Patent SBERT"]
        res = pd.DataFrame([evaluate(m, s, truth) for m, s in sel.items()]).set_index("method")
        res["McNemar p vs A"] = [np.nan if m == "A - keywords" else mcnemar(truth, sel["A - keywords"], sel[m])
                                 for m in res.index]
        show(res, "8.1 Retrieval evaluation (Precision / Recall / F1 table for the technical appendix)")
        res.round(4).to_csv(out / "retrieval_evaluation.csv")

    pool = fam.loc[~fam["in_sector_rules"] & fam["has_en_abstract"],
                   ["docdb_family_id", "family_year", "first_office", "title_en", "abstract_en", "block_combo"]]
    pool = pool.assign(source="excluded candidate")
    if DISCOVERY_INCLUDE_BROAD:
        extra_ids = sorted(broad_outside - set(fam["docdb_family_id"]))
        if extra_ids:
            ab = fetch_by_family(patstat, lambda ids: db.query(TLS201_APPLN.docdb_family_id, TLS203_APPLN_ABSTR.appln_abstract)
                                 .join(TLS203_APPLN_ABSTR, TLS201_APPLN.appln_id == TLS203_APPLN_ABSTR.appln_id)
                                 .filter(TLS203_APPLN_ABSTR.appln_abstract_lg == "en")
                                 .filter(TLS201_APPLN.docdb_family_id.in_(ids)), extra_ids)
            ti = fetch_by_family(patstat, lambda ids: db.query(TLS201_APPLN.docdb_family_id, TLS202_APPLN_TITLE.appln_title)
                                 .join(TLS202_APPLN_TITLE, TLS201_APPLN.appln_id == TLS202_APPLN_TITLE.appln_id)
                                 .filter(TLS202_APPLN_TITLE.appln_title_lg == "en")
                                 .filter(TLS201_APPLN.docdb_family_id.in_(ids)), extra_ids)
            if not ab.empty:
                titles_part = (ti.drop_duplicates("docdb_family_id").rename(columns={"appln_title": "title_en"})
                               if not ti.empty else pd.DataFrame(columns=["docdb_family_id", "title_en"]))
                broad_pool = (ab.drop_duplicates("docdb_family_id").rename(columns={"appln_abstract": "abstract_en"})
                              .merge(titles_part, on="docdb_family_id", how="left")
                              .merge(broad_years, on="docdb_family_id", how="left")
                              .assign(source="broad net only (outside candidate pool)", block_combo="-"))
                pool = pd.concat([pool, broad_pool], ignore_index=True)
        pool.loc[pool["docdb_family_id"].isin(broad_outside) & pool["source"].eq("excluded candidate"),
                 "source"] = "excluded candidate, in the broad net"
    pool = pool.drop_duplicates("docdb_family_id").reset_index(drop=True)
    if len(pool) > DISCOVERY_MAX_POOL:
        pool = pool.sample(DISCOVERY_MAX_POOL, random_state=C.SEED).reset_index(drop=True)
    print(f"\n8.2 Discovery pool: {len(pool):,} families with an English abstract")
    show(pool["source"].value_counts().to_frame("families"))

    texts_pool = pool["abstract_en"].fillna("").tolist()
    ranks = {"BM25 - description": BM25([tokenize(t) for t in texts_pool]).scores(tokenize(description))}
    if use_sbert:
        emb_pool = embed(texts_pool)
        desc_vec = embed([description])[0]
        ranks["SBERT - description"] = emb_pool @ desc_vec
        sector_en = rule_sector[rule_sector["has_en_abstract"]].reset_index(drop=True)
        centroid = embed(sector_en["abstract_en"].fillna("")).mean(axis=0)
        ranks["SBERT - sector centroid"] = emb_pool @ (centroid / np.linalg.norm(centroid))

    for name, score in ranks.items():
        pool[f"score {name}"] = score
        pool[f"rank {name}"] = pd.Series(score).rank(ascending=False, method="first").astype(int).values
    rank_cols = [c for c in pool.columns if c.startswith("rank ")]
    in_top = pool[rank_cols] <= DISCOVERY_TOP_K
    discovery = pool[in_top.any(axis=1)].copy()
    discovery["found_by"] = in_top[in_top.any(axis=1)].apply(
        lambda r: " + ".join(c.replace("rank ", "") for c in rank_cols if r[c]), axis=1)
    discovery["best_rank"] = discovery[rank_cols].min(axis=1)
    discovery = discovery.sort_values("best_rank")

    known = (labels.drop_duplicates("docdb_family_id", keep="last").set_index("docdb_family_id")
             if not labels.empty else pd.DataFrame(columns=[C.LABEL_COL, C.LABEL_COL_BROAD]))
    for c in [C.LABEL_COL, C.LABEL_COL_BROAD]:
        discovery[c] = discovery["docdb_family_id"].map(known[c]) if c in known else np.nan
    discovery["note"] = ""
    discovery["grey_zone"] = ""
    discovery["sample"] = "discovery"
    label_cols = ["docdb_family_id", "sample", "family_year", "title_en", "abstract_en", C.LABEL_COL,
                  C.LABEL_COL_BROAD, "grey_zone", "note"]
    discovery[label_cols].sample(frac=1, random_state=C.SEED).to_csv(out / "label_discovery.csv", index=False)
    discovery[["docdb_family_id", "source", "block_combo", "found_by", "best_rank"] + rank_cols].to_csv(
        out / "discovery_ranks.csv", index=False)
    print(f"label_discovery.csv: {len(discovery)} families (union of the top {DISCOVERY_TOP_K} of each ranking)")
    show(discovery["found_by"].value_counts().to_frame("families"))

    disc_lab = labels[labels["docdb_family_id"].isin(discovery["docdb_family_id"])]
    if disc_lab.empty:
        print("Label label_discovery.csv and save it in labels/ (sample = discovery), then re-run.")
        return disc_lab
    d = discovery.drop(columns=[C.LABEL_COL]).merge(disc_lab[["docdb_family_id", C.LABEL_COL]], on="docdb_family_id")
    rows = []
    for name in ranks:
        for kk in (10, 20, DISCOVERY_TOP_K):
            top = d[d[f"rank {name}"] <= kk]
            rel, n = int(top[C.LABEL_COL].sum()), len(top)
            lo, hi = wilson(rel, n)
            rows.append({"ranking": name, "k": kk, "labelled": n, "relevant": rel,
                         "precision@k": rel / n if n else np.nan, "ci_low": lo, "ci_high": hi})
    p_at_k = pd.DataFrame(rows).set_index(["ranking", "k"]).round(3)
    show(p_at_k, "Precision@k of the rankings outside the sector")
    p_at_k.to_csv(out / "discovery_precision_at_k.csv")
    relevant = d[d[C.LABEL_COL] == 1]
    print(f"Relevant families missed by the rules and found by text retrieval: {len(relevant)} (a lower bound)")
    show(relevant["source"].value_counts().to_frame("relevant"), "by source")
    show(relevant["found_by"].value_counts().to_frame("relevant"), "by ranking")

    if len(relevant) >= 3 and len(d) > len(relevant):
        cv = CountVectorizer(ngram_range=(2, 2), stop_words=list(ENGLISH_STOP_WORDS | PATENT_STOP_WORDS), min_df=2)
        X = cv.fit_transform((d["title_en"].fillna("") + " " + d["abstract_en"].fillna("")).tolist())
        is_rel = (d[C.LABEL_COL] == 1).to_numpy()
        f_rel = np.asarray(X[is_rel].sum(axis=0)).ravel() + 0.5
        f_non = np.asarray(X[~is_rel].sum(axis=0)).ravel() + 0.5
        log_odds = np.log(f_rel / f_rel.sum()) - np.log(f_non / f_non.sum())
        terms = pd.DataFrame({"bigram": cv.get_feature_names_out(), "in relevant": f_rel - 0.5,
                              "in non-relevant": f_non - 0.5, "log-odds": log_odds})
        new_kw = terms[terms["in relevant"] >= 2].sort_values("log-odds", ascending=False).head(20).round(2)
        show(new_kw, "Candidate new keywords")
        new_kw.to_csv(out / "discovery_candidate_keywords.csv", index=False)
    return disc_lab


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-label-samples", action="store_true", help="do not write the samples to hand-label (section 6)")
    ap.add_argument("--retrieval", action="store_true", help="also run the lexical vs semantic experiments (section 8)")
    ap.add_argument("--no-sbert", action="store_true", help="with --retrieval: BM25 and TF-IDF only (no Patent SBERT)")
    args = ap.parse_args(argv)

    C.OUT_STEP0.mkdir(parents=True, exist_ok=True)
    (C.OUT_STEP0 / "raw").mkdir(exist_ok=True)
    patstat = PatstatClient(env=C.ENV)
    db = patstat.orm()
    print(f"Sector: {C.SECTOR_NAME} | env: {C.ENV} | years {C.YEAR_MIN}-{C.YEAR_MAX} "
          f"| last complete year {C.LAST_COMPLETE_YEAR} | IPR type {C.IPR_TYPE}")

    labels = load_labels()
    second_labels = load_second_labels()
    print(f"Labelled families in {C.LABEL_DIR}/: {len(labels):,} | second labellers: "
          f"{[c[4:] for c in second_labels.columns if c.startswith('lab_')]}")
    labelled_relevant = set(labels.loc[labels[C.LABEL_COL] == 1, "docdb_family_id"])
    labelled_not_relevant = set(labels.loc[labels[C.LABEL_COL] == 0, "docdb_family_id"])

    pool_ids, query_pool = build_candidate_pool(patstat, db, labelled_relevant)
    apps, _family_ids, titles, abstracts, cpc, ipc = download_family_data(patstat, db, pool_ids)
    app_units, fam = build_units(apps, titles, abstracts, cpc, ipc)

    fam, sector, rule_sector, flags, gz = select_sector(fam, app_units, labelled_relevant, labelled_not_relevant)
    report_grey_zones_and_coverage(fam, sector, gz, flags)

    block_overlap(rule_sector)
    broad_set, broad_outside, broad_years = broad_net(patstat, db, rule_sector)
    if not args.no_label_samples:
        write_label_samples(patstat, db, fam, rule_sector, labels, broad_outside, broad_years, gz)

    val = validate_precision(fam, rule_sector, labels, gz)
    kappa = agreement_between_labellers(fam, labels, second_labels)
    broad_lab, recall, recall_range = validate_recall(val, labels, broad_outside)
    save_corrections(fam, rule_sector, sector)

    if args.retrieval:
        run_retrieval(patstat, db, fam, rule_sector, val, labels, broad_outside, broad_years,
                      use_sbert=not args.no_sbert)

    save_outputs(fam, sector, flags, gz, apps, titles, abstracts, cpc, ipc)
    summary_table(fam, sector, rule_sector, val, kappa, recall, recall_range, broad_lab, labels)
    prisma(fam, sector, rule_sector, pool_ids, query_pool, val, kappa, recall, recall_range, broad_lab)


if __name__ == "__main__":
    main()
