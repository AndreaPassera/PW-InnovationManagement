import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# relative paths in the config are resolved from the repository root
os.chdir(ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
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
    BLOCKS, EXCLUSIONS, apply_blocks, fetch_by_ids, freeze_requirements, grey_zone_flags,
    regex_to_like_phrases, wilson,
)


def sql_prefix_any(col, prefixes):
    # PATSTAT pads CPC/IPC symbols with blanks
    symbol = func.replace(col, " ", "")
    return or_(*(symbol.like(p.replace(" ", "") + "%") for p in prefixes))


def sql_like_any(col, phrases):
    low = func.lower(col)
    return or_(*(low.like(f"%{p}%") for p in phrases))


def real_pi(q):
    return q.filter(TLS201_APPLN.ipr_type == C.IPR_TYPE,
                    TLS201_APPLN.appln_id > 0, TLS201_APPLN.appln_id < C.INVALID_APPLN_ID)


def load_labels():
    frames = []
    for f in sorted(C.LABEL_DIR.glob("*.csv")):
        d = pd.read_csv(f, encoding="utf-8-sig")
        if C.LABEL_COL not in d.columns or "docdb_family_id" not in d.columns:
            continue
        d = d[pd.to_numeric(d[C.LABEL_COL], errors="coerce").isin([0, 1])].copy()
        d[C.LABEL_COL] = d[C.LABEL_COL].astype(int)
        broad = (pd.to_numeric(d[C.LABEL_COL_BROAD], errors="coerce") if C.LABEL_COL_BROAD in d
                 else pd.Series(np.nan, d.index))
        # the broad rule always includes what the strict rule includes
        d[C.LABEL_COL_BROAD] = np.maximum(broad.fillna(d[C.LABEL_COL]), d[C.LABEL_COL]).astype(int)
        d["docdb_family_id"] = d["docdb_family_id"].astype("int64")
        d["label_file"] = f.name
        frames.append(d)
    cols = ["docdb_family_id", C.LABEL_COL, C.LABEL_COL_BROAD, "label_file", "sample"]
    if not frames:
        return pd.DataFrame(columns=cols)
    out = pd.concat(frames, ignore_index=True).drop_duplicates("docdb_family_id", keep="last")
    out["sample"] = out["sample"].fillna("").astype(str).str.strip() if "sample" in out else ""
    return out.reset_index(drop=True)


def build_candidate_pool(patstat, db, labelled_relevant):
    family_id = TLS201_APPLN.docdb_family_id.label("docdb_family_id")
    kw_phrases = regex_to_like_phrases(C.SPECIFIC_KEYWORDS + C.GENERIC_KEYWORDS) + list(C.BLOCK6_SQL_PHRASES)

    # wide on purpose: SQL LIKE cannot match whole words, the exact rules run later in pandas
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
    pool_acronyms = real_pi(db.query(family_id)
                            .join(TLS202_APPLN_TITLE, TLS201_APPLN.appln_id == TLS202_APPLN_TITLE.appln_id)
                            .filter(or_(*(title_low.like(p) for p in C.BLOCK5_SQL_PATTERNS))))

    ids = pd.DataFrame(patstat.df(pool_cpc.union(pool_title, pool_abstract, pool_acronyms)))
    query_pool = set(ids["docdb_family_id"].dropna().astype("int64"))
    pool_ids = sorted(query_pool | labelled_relevant)
    print(f"Candidate pool: {len(query_pool):,} families from the query + "
          f"{len(set(pool_ids) - query_pool):,} labelled relevant outside it")
    return pool_ids


def download_family_data(patstat, db, pool_ids):
    apps = fetch_by_ids(
        patstat,
        lambda ids: real_pi(db.query(
            TLS201_APPLN.docdb_family_id, TLS201_APPLN.inpadoc_family_id, TLS201_APPLN.appln_id,
            TLS201_APPLN.appln_auth, TLS201_APPLN.appln_kind, TLS201_APPLN.appln_filing_date,
            TLS201_APPLN.appln_filing_year, TLS201_APPLN.earliest_filing_year, TLS201_APPLN.earliest_publn_year,
            TLS201_APPLN.granted, TLS201_APPLN.docdb_family_size, TLS201_APPLN.nb_citing_docdb_fam,
        ).filter(TLS201_APPLN.docdb_family_id.in_(ids))),
        pool_ids,
    )
    # family year = earliest priority year over the applications of the family
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
        return fetch_by_ids(patstat, make, family_ids)

    titles = by_family(TLS202_APPLN_TITLE, TLS202_APPLN_TITLE.appln_id, TLS202_APPLN_TITLE.appln_title,
                       TLS202_APPLN_TITLE.appln_title_lg)
    abstracts = by_family(TLS203_APPLN_ABSTR, TLS203_APPLN_ABSTR.appln_id, TLS203_APPLN_ABSTR.appln_abstract,
                          TLS203_APPLN_ABSTR.appln_abstract_lg)
    cpc = by_family(TLS224_APPLN_CPC, TLS224_APPLN_CPC.appln_id, TLS224_APPLN_CPC.cpc_class_symbol)
    cpc["cpc"] = cpc["cpc_class_symbol"].str.replace(" ", "", regex=False)
    return apps, titles, abstracts, cpc


def build_family_table(apps, titles, abstracts, cpc):
    def join_unique(s):
        return " || ".join(str(v).strip() for v in s.dropna().unique() if str(v).strip())

    def as_list(v):
        return v if isinstance(v, list) else []

    def english_first(df, text_col, english_only=True):
        text = df[text_col].fillna("").astype(str).str.strip()
        ok = text.ne("")
        if english_only:
            ok &= df[text_col + "_lg"].fillna("").astype(str).str.strip().str.lower().eq("en")
        return df[ok].drop_duplicates("docdb_family_id").set_index("docdb_family_id")[text_col]

    # texts and codes of all the applications of a family are pooled
    fam = (apps.groupby("docdb_family_id")
           .agg(family_year=("family_year", "first"), n_applications=("appln_id", "nunique"),
                docdb_family_size=("docdb_family_size", "max"))
           .reset_index())
    first_app = apps.sort_values(["docdb_family_id", "appln_filing_date", "appln_id"]).drop_duplicates("docdb_family_id")
    fam["first_office"] = fam["docdb_family_id"].map(first_app.set_index("docdb_family_id")["appln_auth"])
    fam["titles"] = fam["docdb_family_id"].map(titles.groupby("docdb_family_id")["appln_title"].agg(join_unique)).fillna("")
    fam["abstracts"] = fam["docdb_family_id"].map(abstracts.groupby("docdb_family_id")["appln_abstract"].agg(join_unique)).fillna("")
    codes = cpc.groupby("docdb_family_id")["cpc"].agg(lambda s: sorted(set(s)))
    fam["cpc_codes"] = fam["docdb_family_id"].map(codes).apply(as_list)

    # English text is kept for labelling and NLP only, it never filters the sector
    fam["abstract_en"] = fam["docdb_family_id"].map(english_first(abstracts, "appln_abstract"))
    fam["title_en"] = (fam["docdb_family_id"].map(english_first(titles, "appln_title"))
                       .fillna(fam["docdb_family_id"].map(english_first(titles, "appln_title", english_only=False))))
    fam["has_en_abstract"] = fam["abstract_en"].notna()
    print(f"Families: {len(fam):,} | with English abstract: {fam['has_en_abstract'].mean():.1%}")
    return fam


def select_sector(fam, labelled_relevant, labelled_not_relevant):
    flags = BLOCKS + ["has_cpc"] + EXCLUSIONS
    blocks = apply_blocks(fam)
    for col in flags + ["matched", "excluded", "n_blocks", "block_combo"]:
        fam[col] = blocks[col].values
    fam["in_sector_rules"] = blocks["in_sector"].values
    fam = pd.concat([fam, grey_zone_flags(fam)], axis=1)
    fam["is_truncated"] = fam["family_year"] > C.LAST_COMPLETE_YEAR

    # label 0 takes a family out of the sector, label 1 brings one in
    fam["corr_removed"] = C.USE_LABEL_CORRECTIONS & fam["in_sector_rules"] & fam["docdb_family_id"].isin(labelled_not_relevant)
    fam["corr_added"] = C.USE_LABEL_CORRECTIONS & ~fam["in_sector_rules"] & fam["docdb_family_id"].isin(labelled_relevant)
    fam["in_sector"] = (fam["in_sector_rules"] & ~fam["corr_removed"]) | fam["corr_added"]
    fam.loc[fam["corr_added"], "block_combo"] = "added by label"

    sector = fam[fam["in_sector"]].copy()
    rule_sector = fam[fam["in_sector_rules"]].copy()
    if "b3_dropped_no_context" in blocks.columns:
        print(f"Left out by the block 3 context rule: {int((blocks['b3_dropped_no_context'] & ~blocks['matched']).sum()):,}")
    print(f"Selected by the blocks: {fam['matched'].sum():,} | removed by exclusion rules: "
          f"{(fam['matched'] & fam['excluded']).sum():,} | sector given by the rules: {len(rule_sector):,}")
    print(f"Label corrections: -{fam['corr_removed'].sum():,} / +{fam['corr_added'].sum():,}")
    print(f"SECTOR: {len(sector):,} families | truncated (> {C.LAST_COMPLETE_YEAR}): {sector['is_truncated'].sum():,}")
    return fam, sector, rule_sector, flags


def broad_net(patstat, db, rule_sector):
    # generic query used only to estimate what the blocks miss
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
    years = fetch_by_ids(
        patstat,
        lambda ids: real_pi(db.query(TLS201_APPLN.docdb_family_id,
                                     func.min(TLS201_APPLN.earliest_filing_year).label("family_year"))
                            .filter(TLS201_APPLN.docdb_family_id.in_(ids))
                            .filter(TLS201_APPLN.earliest_filing_year != C.MISSING_YEAR))
        .group_by(TLS201_APPLN.docdb_family_id),
        broad_ids,
    )
    broad_set = set(years.loc[years["family_year"].between(C.YEAR_MIN, C.YEAR_MAX), "docdb_family_id"])
    outside = broad_set - set(rule_sector["docdb_family_id"])
    print(f"Broad net: {len(broad_set):,} families | outside the sector: {len(outside):,}")
    return outside


def stratified_precision(rule_sector, lab, col):
    # stratum precisions weighted by the share of the rule sector in each stratum
    sizes = rule_sector["stratum"].value_counts()
    rows = []
    for h, n_total in sizes.items():
        d = lab[lab["stratum"] == h]
        k, n = int(d[col].sum()), len(d)
        lo, hi = wilson(k, n) if n else (np.nan, np.nan)
        rows.append({"stratum": h, "families": n_total, "labelled": n, "relevant": k,
                     "precision": k / n if n else np.nan, "ci_low": lo, "ci_high": hi})
    table = pd.DataFrame(rows).set_index("stratum")
    ok = table[table["labelled"] > 0]
    w = ok["families"] / ok["families"].sum()
    precision = float((w * ok["precision"]).sum())
    ci = (float((w * ok["ci_low"]).sum()), float((w * ok["ci_high"]).sum()))
    return precision, ci, table


def validate(rule_sector, labels, broad_outside):
    # the rule sector is validated before the label corrections, which use the same labels
    stratum = np.where(rule_sector["b6_vehicle_task"] & (rule_sector["n_blocks"] == 1), "block 6 only", "blocks 1-5")
    rule_sector = rule_sector.assign(stratum=stratum)
    shared = [c for c in labels.columns if c in rule_sector.columns and c != "docdb_family_id"]
    lab = rule_sector.merge(labels.drop(columns=shared), on="docdb_family_id", how="inner")
    lab = lab[lab["sample"].isin(C.PRECISION_SAMPLES) & ~lab["label_file"].isin(C.VALIDATION_EXCLUDE_FILES)]
    broad_lab = labels[labels["docdb_family_id"].isin(broad_outside) & (labels["sample"] == "broad_net_outside")
                       & ~labels["label_file"].isin(C.VALIDATION_EXCLUDE_FILES)]
    if lab.empty or broad_lab.empty:
        print("Validation skipped: no labels for the sector or for the broad-net sample")
        return {}

    p, ci, strata = stratified_precision(rule_sector, lab, C.LABEL_COL)
    p_broad, ci_broad, _ = stratified_precision(rule_sector, lab, C.LABEL_COL_BROAD)
    print(f"\nPrecision (strict rule, stratified): {p:.1%} (95% CI {ci[0]:.1%}-{ci[1]:.1%}) on {len(lab)} labelled families")
    print(f"Precision (broad rule, upper bound): {p_broad:.1%} (95% CI {ci_broad[0]:.1%}-{ci_broad[1]:.1%})")
    print(strata.round(3).to_string())

    # recall: relevant share in the labelled broad-net sample, scaled to everything the sector leaves out
    k, n = int(broad_lab[C.LABEL_COL].sum()), len(broad_lab)
    lo, hi = wilson(k, n)
    kept = p * len(rule_sector)
    missed = k / n * len(broad_outside)
    recall = kept / (kept + missed)
    low = ci[0] * len(rule_sector) / (ci[0] * len(rule_sector) + hi * len(broad_outside))
    high = ci[1] * len(rule_sector) / (ci[1] * len(rule_sector) + lo * len(broad_outside))
    print(f"Recall vs broad net: {recall:.1%} (range {low:.1%}-{high:.1%}) on {n} labelled broad-net families")
    strata.round(4).to_csv(C.OUT_STEP0 / "precision_strata.csv")
    return {"precision": p, "precision_ci_low": ci[0], "precision_ci_high": ci[1], "precision_broad_rule": p_broad,
            "recall": recall, "recall_low": low, "recall_high": high}


def save_outputs(fam, sector, flags, apps, titles, abstracts, cpc, stats):
    out = C.OUT_STEP0
    gz = [c for c in fam.columns if c.startswith("gz_")]
    cols = (["docdb_family_id", "family_year", "is_truncated", "first_office", "has_en_abstract"] + flags
            + ["matched", "excluded", "in_sector_rules", "corr_removed", "corr_added", "n_blocks", "block_combo",
               "in_sector"] + gz)
    sector[cols].to_csv(out / "sector_families.csv", index=False)
    fam[cols].to_csv(out / "candidates_all.csv", index=False)

    # raw tables are saved for the sector families only
    ids = set(sector["docdb_family_id"])
    raw = out / "raw"
    apps[apps["docdb_family_id"].isin(ids)].to_csv(raw / "applications.csv.gz", index=False)
    titles[titles["docdb_family_id"].isin(ids)].to_csv(raw / "titles.csv.gz", index=False)
    abstracts[abstracts["docdb_family_id"].isin(ids)].to_csv(raw / "abstracts.csv.gz", index=False)
    cpc[cpc["docdb_family_id"].isin(ids)][["docdb_family_id", "appln_id", "cpc"]].to_csv(raw / "cpc.csv.gz", index=False)

    summary = {"families_in_window": len(fam), "selected_by_blocks": int(fam["matched"].sum()),
               "removed_by_exclusions": int((fam["matched"] & fam["excluded"]).sum()),
               "sector_by_rules": int(fam["in_sector_rules"].sum()), "removed_by_labels": int(fam["corr_removed"].sum()),
               "added_by_labels": int(fam["corr_added"].sum()), "final_sector": len(sector),
               "final_sector_truncated": int(sector["is_truncated"].sum()), **stats}
    pd.Series(summary, name="value").to_csv(out / "step0_summary.csv")
    print(f"\nSaved in {out.resolve()}")
    freeze_requirements(out / "environment.txt")


def main():
    C.OUT_STEP0.mkdir(parents=True, exist_ok=True)
    (C.OUT_STEP0 / "raw").mkdir(exist_ok=True)
    patstat = PatstatClient(env=C.ENV)
    db = patstat.orm()
    print(f"Sector: {C.SECTOR_NAME} | env: {C.ENV} | years {C.YEAR_MIN}-{C.YEAR_MAX} | IPR type {C.IPR_TYPE}")

    # labels go first: families labelled relevant are downloaded even if the query misses them
    labels = load_labels()
    labelled_relevant = set(labels.loc[labels[C.LABEL_COL] == 1, "docdb_family_id"])
    labelled_not_relevant = set(labels.loc[labels[C.LABEL_COL] == 0, "docdb_family_id"])

    pool_ids = build_candidate_pool(patstat, db, labelled_relevant)
    apps, titles, abstracts, cpc = download_family_data(patstat, db, pool_ids)
    fam = build_family_table(apps, titles, abstracts, cpc)
    fam, sector, rule_sector, flags = select_sector(fam, labelled_relevant, labelled_not_relevant)

    stats = validate(rule_sector, labels, broad_net(patstat, db, rule_sector))
    save_outputs(fam, sector, flags, apps, titles, abstracts, cpc, stats)


if __name__ == "__main__":
    main()
