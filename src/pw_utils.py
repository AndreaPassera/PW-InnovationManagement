"""
Reusable functions of the Step 0 pipeline.

Kept out of the notebooks so that (i) both notebooks use the same rules and (ii) the rules can be unit-tested
without PATSTAT (tests/test_rules.py). Functions that query PATSTAT take the client as an argument.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

# ============================================================================ PATSTAT helpers


def get_model(name):
    """Return a PATSTAT table model by name, or None if this tipdata version does not have it."""
    from epo.tipdata.patstat.database import models
    return getattr(models, name, None)


def chunks(seq, size=C.CHUNK):
    # numpy integers (from .unique(), .values) are not accepted as query
    # parameters by the BigQuery driver: convert them to plain Python ints
    seq = [x.item() if hasattr(x, "item") else x for x in seq]
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def fetch_by_ids(patstat, make_query, ids, size=C.CHUNK):
    """Run make_query(chunk) for each chunk of ids and concatenate the results.
    Long IN (...) lists are slow or rejected on PROD, so ids are sent in chunks."""
    frames = [pd.DataFrame(patstat.df(make_query(ch))) for ch in chunks(ids, size)]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def available_columns(model, names):
    """Columns of `model` among `names` (PATSTAT editions differ slightly)."""
    return [getattr(model, n) for n in names if hasattr(model, n)]


# ============================================================================ text matching (sector query)


def compile_any(patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns))


def regex_to_like_phrases(patterns):
    """Plain phrases for the SQL candidate pool, derived from the regex keywords.
    Generous on purpose (alternatives expanded, separators as space and hyphen): Python does the exact match."""
    phrases = set()
    for p in patterns:
        p = p.replace(r"\b", "").replace("s?", "").replace("[\\s-]+", " ").replace("[\\s-]?", " ")
        m = re.search(r"\(\?:([^)]*)\)", p)
        variants = [p.replace(m.group(0), alt) for alt in m.group(1).split("|")] if m else [p]
        for v in variants:
            v = re.sub(r"\s+", " ", v).strip()
            phrases.update({v, v.replace(" ", "-")})
    return sorted(phrases)


def has_code(codes, prefixes):
    return any(c.startswith(p) for c in codes for p in prefixes)


def apply_blocks(units):
    """Add the flags of the sector query to a table of units (applications or families).

    `units` needs: titles, abstracts (text of the unit) and cpc_codes (list of blank-free symbols).
    Returns a copy with b1..b5, has_cpc, the exclusion flags (ex_*), excluded, in_sector, n_blocks, block_combo.
    With one row per application, code and words must sit on the same application; with one row per family the
    texts and codes of all its applications are pooled.
    """
    rx_under, rx_spec = compile_any(C.UNDERWATER_TERMS), compile_any(C.SPECIFIC_KEYWORDS)
    rx_gen, rx_robot, rx_b5 = compile_any(C.GENERIC_KEYWORDS), compile_any(C.ROBOT_TERMS), compile_any(C.BLOCK5_TITLE_ACRONYMS)
    out = units.copy()
    title = out["titles"].fillna("").str.lower()
    abstract = out["abstracts"].fillna("").str.lower()
    text = title + " || " + abstract
    codes = out["cpc_codes"]

    out["b1_cpc"] = codes.apply(has_code, prefixes=C.BLOCK1_CPC) & text.str.contains(rx_under)
    out["b2_specific"] = text.str.contains(rx_spec)
    b3_context = text.str.contains(compile_any(getattr(C, "BLOCK3_CONTEXT_TERMS", C.UNDERWATER_TERMS))) | \
        codes.apply(has_code, prefixes=getattr(C, "BLOCK3_CONTEXT_CPC", ("B63",)))
    b3_words = title.str.contains(rx_gen) | (abstract.str.contains(rx_gen) & codes.apply(has_code, prefixes=C.BLOCK3_CPC))
    out["b3_generic"] = b3_words & b3_context
    out["b3_dropped_no_context"] = b3_words & ~b3_context        # v5 review: block 3 words without a water context
    out["b4_neighbour"] = codes.apply(has_code, prefixes=C.BLOCK4_CPC) & text.str.contains(rx_robot)
    out["b5_acronym_title"] = title.str.contains(rx_b5)
    out["b6_vehicle_task"] = (text.str.contains(compile_any(C.BLOCK6_OBJECT)) & text.str.contains(compile_any(C.BLOCK6_TASK))) \
        | text.str.contains(compile_any(C.BLOCK6_STANDALONE))
    out["has_cpc"] = codes.apply(len) > 0
    out = pd.concat([out, exclusion_flags(title)], axis=1)
    return summarise_blocks(out)


BLOCKS = ["b1_cpc", "b2_specific", "b3_generic", "b4_neighbour", "b5_acronym_title", "b6_vehicle_task"]
EXCLUSIONS = [f"ex_{k}" for k in C.EXCLUSION_RULES]
FLAG_NAMES = BLOCKS + ["has_cpc"] + EXCLUSIONS


def exclusion_flags(title):
    """Grey-zone exclusion rules on the (lower-case) title: a rule fires when its pattern matches and the title has
    no vehicle term (config.KEEP_TERMS)."""
    keep = title.str.contains(compile_any(C.KEEP_TERMS))
    return pd.DataFrame({f"ex_{k}": title.str.contains(re.compile(p)) & ~keep
                         for k, p in C.EXCLUSION_RULES.items()}, index=title.index)


def summarise_blocks(df):
    """matched (any block), excluded (any exclusion rule), in_sector, n_blocks and block_combo from the flags."""
    out = df.copy()
    out["matched"] = out[BLOCKS].any(axis=1)
    out["excluded"] = out[[c for c in EXCLUSIONS if c in out]].any(axis=1) if any(c in out for c in EXCLUSIONS) else False
    out["in_sector"] = out["matched"] & ~out["excluded"]
    out["n_blocks"] = out[BLOCKS].sum(axis=1)
    out["block_combo"] = out[BLOCKS].apply(
        lambda r: " + ".join(b.split("_")[0] for b in BLOCKS if r[b]) or "-", axis=1)
    return out


def flags_by_family(app_flags, family_col="docdb_family_id"):
    """Family-level flags from application-level ones: a family carries a block (or an exclusion) if one of its
    applications does."""
    cols = [c for c in FLAG_NAMES if c in app_flags]
    return app_flags.groupby(family_col)[cols].any()


def grey_zone_flags(units):
    """Topic flags (config.GREY_ZONE_PATTERNS, plus offshore-wind CPC codes): they count the grey zones, never
    change the sector."""
    text = (units["titles"].fillna("") + " " + units["abstracts"].fillna("")).str.lower()
    out = pd.DataFrame({f"gz_{k}": text.str.contains(re.compile(p)) for k, p in C.GREY_ZONE_PATTERNS.items()},
                       index=units.index)
    if "cpc_codes" in units:
        out["gz_offshore_wind"] |= units["cpc_codes"].apply(has_code, prefixes=C.OFFSHORE_WIND_CPC)
    return out


# ============================================================================ statistics


def wilson(k, n, z=1.96):
    """Wilson 95% interval for a proportion k/n."""
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    den = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / den
    return (centre - half, centre + half)


def precision_table(df, by, label=C.LABEL_COL):
    g = df.groupby(by, dropna=False, observed=True)[label].agg(["size", "sum"]).rename(
        columns={"size": "n", "sum": "relevant"})
    g["precision"] = g["relevant"] / g["n"]
    ci = [wilson(k, n) for k, n in zip(g["relevant"], g["n"])]
    g["ci_low"], g["ci_high"] = [c[0] for c in ci], [c[1] for c in ci]
    return g.round(3)


# ============================================================================ applicants

_SUFFIX_RX = re.compile(
    r"(\s+(" + "|".join(sorted(map(re.escape, C.LEGAL_SUFFIXES), key=len, reverse=True)) + r"))+\s*$")
_GROUP_RX = [(re.compile(k), v) for k, v in C.GROUP_ALIASES.items()]


_PREFIX_RX = re.compile(r"^(" + "|".join(sorted(map(re.escape, C.LEGAL_PREFIXES), key=len, reverse=True)) + r")\s+")


def normalise_name(name):
    """Upper case, punctuation removed, legal forms removed at the end AND at the start (Tutorial 1, method A):
    'Kongsberg Maritime AS' -> 'KONGSBERG MARITIME', 'KABUSHIKI KAISHA TOSHIBA' -> 'TOSHIBA'."""
    if name is None or (isinstance(name, float) and np.isnan(name)) or pd.isna(name) or not str(name).strip():
        return ""
    s = str(name).upper()
    s = re.sub(r"[.,;:'\"()&/\\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _SUFFIX_RX.sub("", s).strip()
    return _PREFIX_RX.sub("", s).strip() or s


def to_group(norm):
    """Corporate group of a normalised name (GROUP_ALIASES), or the name itself."""
    for rx, group in _GROUP_RX:
        if rx.search(norm):
            return group
    return norm


def applicant_type(psn_sector, name):
    """firm / university / government / hospital / individual / unknown.

    psn_sector first, checked in the order of Tutorial 1 (Notebook 11, 2.3): UNIVERSITY, COMPANY, GOV, HOSPITAL,
    INDIVIDUAL, so "COMPANY UNIVERSITY" is a university and "COMPANY GOV NON-PROFIT" a firm. Only when psn_sector is
    empty or UNKNOWN does the name decide (universities and research institutes)."""
    sec = str(psn_sector).upper() if pd.notna(psn_sector) else ""
    name = name or ""
    if "UNIVERSITY" in sec:
        return "university"
    if "COMPANY" in sec:
        return "firm"
    if "GOV" in sec:
        return "government / non-profit"
    if "HOSPITAL" in sec:
        return "hospital"
    if "INDIVIDUAL" in sec:
        return "individual"
    if re.search(r"\bUNIV|\bCOLLEGE|\bSCHOOL\b|大学", name):
        return "university"
    if re.search(r"\bINST(ITUTE)?\b|\bACADEMY\b|\bRESEARCH CENT|\bLAB(ORATORY)?\b", name):
        return "government / non-profit"
    # v7: psn_sector is empty for most Chinese and Russian applicants, so public bodies and companies are also
    # recognised from the name (legal forms such as CO LTD are already stripped by normalise_name)
    if re.search(C.GOV_NAME_PATTERN, name):
        return "government / non-profit"
    if re.search(C.FIRM_NAME_PATTERN, name):
        return "firm"
    return "unknown"


def clean_country(code):
    """PATSTAT country codes can be NULL, '' or blanks: all treated as unknown (Tutorial 0, Notebook 2)."""
    if pd.isna(code) or not str(code).strip():
        return pd.NA
    return str(code).strip()


# ============================================================================ text for NLP

PATENT_STOP_WORDS = {
    "said", "wherein", "whereby", "thereof", "therein", "comprising", "comprises", "comprise", "including",
    "includes", "plurality", "least", "configured", "provided", "providing", "present", "invention", "disclosed",
    "disclosure", "embodiment", "embodiments", "method", "methods", "device", "devices", "apparatus", "system",
    "systems", "unit", "first", "second", "third", "one", "having", "based",
}


def strip_tags(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text))).strip()


def clean_patent_text(text, stop_words=None):
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    stop = stop_words if stop_words is not None else (ENGLISH_STOP_WORDS | PATENT_STOP_WORDS)
    if pd.isna(text) or not str(text).strip():
        return ""
    words = re.findall(r"[a-z][a-z\-]+", strip_tags(text).lower())
    return " ".join(w for w in words if w not in stop)


# ============================================================================ legal events


def legal_event_flags(text):
    """Event families (renewal, lapse, opposition, transfer...) matched in event category titles / texts.
    `text` is a pandas Series; returns one boolean column per family of config.LEGAL_EVENT_PATTERNS."""
    t = pd.Series(text).fillna("").astype(str).str.lower().str.replace(r"\s+", " ", regex=True)
    return pd.DataFrame({k: t.str.contains(p, regex=True) for k, p in C.LEGAL_EVENT_PATTERNS.items()}, index=t.index)


# ============================================================================ BM25 (lexical retrieval, Tutorial 0)

TOKEN_PATTERN = re.compile(r"\b[a-zA-Z0-9]+\b")


def tokenize(text):
    """Lower-case alphanumeric tokens, as in Tutorial 0 (Notebook 1, section 6.4)."""
    return TOKEN_PATTERN.findall(str(text).lower()) if pd.notna(text) else []


class BM25:
    """Okapi BM25 (k1 = 1.5, b = 0.75, the defaults of rank_bm25.BM25Okapi used in Tutorial 0).

    Implemented here so it can be unit-tested and needs no extra package. The idf is
    ln((N - n + 0.5) / (n + 0.5) + 1), which is never negative (rank_bm25 instead floors negative idf
    with an epsilon): rankings are the same for any term that appears in fewer than half of the documents.
    """

    def __init__(self, corpus_tokens, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [pd.Series(t, dtype="object").value_counts().to_dict() for t in corpus_tokens]
        self.len = np.array([len(t) for t in corpus_tokens], dtype=float)
        self.avg = self.len.mean() if len(self.len) and self.len.mean() > 0 else 1.0
        n_docs = len(self.docs)
        df = {}
        for d in self.docs:
            for w in d:
                df[w] = df.get(w, 0) + 1
        self.idf = {w: np.log((n_docs - n + 0.5) / (n + 0.5) + 1) for w, n in df.items()}

    def scores(self, query_tokens):
        q = set(query_tokens)
        norm = self.k1 * (1 - self.b + self.b * self.len / self.avg)
        out = np.zeros(len(self.docs))
        for i, d in enumerate(self.docs):
            s = 0.0
            for w in q:
                f = d.get(w)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + norm[i])
            out[i] = s
        return out


# ============================================================================ counting rules (Tutorial 0, Notebook 2, section 4)


def fractional_weights(df, unit, entity, require=None):
    """Fractional counting: each unit (family or application) is split equally among its distinct entities.

    - countries: entity = person (or applicant group), require = "country": persons with unknown country are
      dropped first, so the known countries of a unit sum to exactly 1 (2 IT + 1 DE applicants -> 2/3 IT, 1/3 DE);
    - technology classes: entity = CPC subclass (a family in 4 subclasses gives 1/4 to each).
    Returns the de-duplicated rows with a `weight` column.
    """
    d = df.drop_duplicates([unit, entity])
    if require is not None:
        d = d[d[require].notna()]
    d = d.copy()
    d["weight"] = 1 / d.groupby(unit)[entity].transform("size")
    return d


# ----------------------------------------------------------------------------- reproducibility
REQUIREMENT_PACKAGES = ("pandas", "numpy", "matplotlib", "scipy", "scikit-learn", "networkx", "PyYAML", "SQLAlchemy",
                        "sentence-transformers", "umap-learn", "bertopic", "pytest")
TIP_ONLY_PACKAGES = ("epo-tipdata", "epo-tipdata-patstat", "epo.tipdata")


def freeze_requirements(path, packages=REQUIREMENT_PACKAGES):
    """Write requirements.txt with the EXACT versions installed in the running environment (called at the end of
    01_extract, i.e. on TIP: the file then reproduces the environment the numbers come from). Packages not installed
    are listed as comments; the PATSTAT client is TIP-only and is recorded as a comment."""
    import platform
    from datetime import date
    from importlib.metadata import PackageNotFoundError, version
    lines = [f"# Generated by pw_utils.freeze_requirements on {date.today()} (Python {platform.python_version()}).",
             "# Exact versions of the environment the pipeline ran in. Fresh environment: pip install -r requirements.txt"]
    for p in packages:
        try:
            lines.append(f"{p}=={version(p)}")
        except PackageNotFoundError:
            lines.append(f"# {p}: not installed in this environment")
    for p in TIP_ONLY_PACKAGES:
        try:
            lines.append(f"# {p}=={version(p)}  (PATSTAT client, preinstalled on TIP only: needed by 01_extract and 02_clean)")
            break
        except PackageNotFoundError:
            continue
    else:
        lines.append("# epo.tipdata (PATSTAT client): preinstalled on TIP only, needed by 01_extract and 02_clean")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


# ----------------------------------------------------------------------------- name harmonisation: evaluation
def pairwise_prf(gold, pred):
    """Pairwise precision, recall and F1 of a grouping against a gold standard (Tutorial 1, nb 11 §2.4): a pair of
    names is a true positive when it is in the same predicted group AND in the same gold group."""
    from math import comb
    d = pd.DataFrame({"gold": list(gold), "pred": list(pred)})
    pairs = lambda sizes: sum(comb(int(n), 2) for n in sizes)
    tp = pairs(d.groupby(["gold", "pred"]).size())
    merged, true = pairs(d.groupby("pred").size()), pairs(d.groupby("gold").size())
    p = tp / merged if merged else np.nan
    r = tp / true if true else np.nan
    f1 = 2 * p * r / (p + r) if p and r and p + r else 0.0
    return {"precision": p, "recall": r, "F1": f1}


def harmonisation_scores(gold, pred):
    """pairwise_prf plus number of groups found, homogeneity, completeness and V-measure (sklearn)."""
    from sklearn.metrics import homogeneity_completeness_v_measure
    h, c, v = homogeneity_completeness_v_measure(list(gold), list(pred))
    return {"groups found": int(pd.Series(list(pred)).nunique()), **pairwise_prf(gold, pred),
            "homogeneity": h, "completeness": c, "V-measure": v}
