"""Offline end-to-end run of 01_extract and 02_clean on a fake PATSTAT (see fakelibs.py).

Run from the repository root:  python tests/offline/run_offline.py
It executes every code cell of the three notebooks on synthetic data (results land in tests/offline/run/, git-ignored).
It catches wrong PATSTAT column names, broken query chains and pandas errors before using TIP; it does NOT
replace the first run on TIP TEST, where the real SQL is executed. The benchmarks of 02_clean (sub-queries) are skipped.
"""
import re
import shutil
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1]          # repository root
RUN = HERE / "run"
shutil.rmtree(RUN, ignore_errors=True)
RUN.mkdir()
for f in ["config.py", "pw_utils.py"]:
    shutil.copy(SRC / f, RUN / f)
(RUN / "labels").mkdir()

sys.path.insert(0, str(HERE))
import fakelibs as F  # noqa: E402
F.install()

# ---------------------------------------------------------------- fake sentence-transformers and umap
st = types.ModuleType("sentence_transformers")


class _ST:
    def __init__(self, *a, **k):
        pass

    def encode(self, texts, **k):
        texts = [texts] if isinstance(texts, str) else list(texts)
        out = []
        for t in texts:
            v = np.zeros(64)
            for w in re.findall(r"[a-z]+", str(t).lower()):
                v[hash(w) % 64] += 1
            v += 0.01
            out.append(v / np.linalg.norm(v))
        return np.array(out)


st.SentenceTransformer = _ST
sys.modules["sentence_transformers"] = st
um = types.ModuleType("umap")


class _U:
    def __init__(self, **k):
        pass

    def fit_transform(self, X):
        X = X - X.mean(0)
        return X @ np.linalg.svd(X, full_matrices=False)[2][:2].T


um.UMAP = _U
sys.modules["umap"] = um

# ---------------------------------------------------------------- synthetic PATSTAT
rng = np.random.default_rng(7)
TEMPLATES = [  # title, abstract, cpc, ipc, relevant
    ("Docking station for an autonomous vehicle", "an underwater vehicle docks and charges its battery", ["B63G2008/004"], ["B63G8/00"], 1),
    ("Unmanned underwater glider with buoyancy engine", "glider for ocean sampling", ["B63G8/14"], [], 1),
    ("Remotely operated vehicle manipulator", "a manipulator arm for subsea intervention", ["B25J15/00"], [], 1),
    ("Hull cleaning robot", "a robot attaches to the hull and cleans it", ["B63G8/00", "B63B59/08"], [], 1),
    ("Wind turbine blade", "a composite blade for a turbine", ["B63G2008/002", "F03D1/06"], [], 0),
    ("Unmanned surface ship", "an unmanned ship for patrol", ["B63G8/00"], [], 0),
    ("ROV tether management system", "the tether is wound on a winch", [], [], 1),
    ("Robot for pipeline repair", "a robot travels along the pipeline", [], ["B63C11/52"], 1),
    ("ROV for offshore wind farm inspection", "inspects monopile foundations of the offshore wind farm", ["B63C11/52"], [], 1),
    ("Sonar imaging system", "an acoustic array images the sea bottom", [], ["G01S15/89"], 0),
    ("Crewed submarine pressure hull", "an underwater vehicle with a crew compartment", ["B63G8/00"], [], 0),
    ("Trans-medium aerial underwater vehicle", "a drone flies and dives under water", ["B64U10/70", "B63G2008/004"], [], 1),
]
fams, apps, titles, abstracts, cpcs, ipcs = [], [], [], [], [], []
aid, fid = 1000, 50000
for rep in range(8):
    for ti, (t, a, cpc, ipc, rel) in enumerate(TEMPLATES):
        fid += 1
        year = int(rng.integers(2001, 2026))
        n_apps = int(rng.integers(1, 4))
        offices = ["CN", "US", "EP", "WO", "JP", "KR"]
        for k in range(n_apps):
            aid += 1
            auth = rng.choice(offices) if k else rng.choice(["CN", "US", "EP", "KR"])
            apps.append(dict(appln_id=aid, docdb_family_id=fid, inpadoc_family_id=fid, appln_auth=auth,
                             appln_kind="W " if auth == "WO" else "A ", appln_filing_date=f"{year + k}-02-01",
                             appln_filing_year=year + k, earliest_filing_year=year, earliest_publn_year=year + 1 + k,
                             granted="Y" if rng.random() < .5 else "N", docdb_family_size=n_apps,
                             nb_citing_docdb_fam=int(rng.poisson(1)), ipr_type="PI"))
            titles.append(dict(appln_id=aid, appln_title_lg="en", appln_title=f"{t} {rep}"))
            abstracts.append(dict(appln_id=aid, appln_abstract_lg="en", appln_abstract=f"{a}. Variant {rep}."))
            for c in cpc:
                cpcs.append(dict(appln_id=aid, cpc_class_symbol=c.replace("/", " /", 1).replace("G2008", "G2008")))
            for c in ipc:
                ipcs.append(dict(appln_id=aid, ipc_class_symbol=c))
        fams.append(dict(docdb_family_id=fid, template=ti, relevant=rel, year=year))
# a family whose code is on a Chinese application and whose English words are on a US one without codes
fid += 1
for k, (auth, t, a, codes) in enumerate([("CN", "水下航行器对接装置", "", ["B63G2008/004"]),
                                          ("US", "Docking device", "the underwater vehicle docks", [])]):
    aid += 1
    apps.append(dict(appln_id=aid, docdb_family_id=fid, inpadoc_family_id=fid, appln_auth=auth, appln_kind="A ",
                     appln_filing_date="2015-03-01", appln_filing_year=2015, earliest_filing_year=2015,
                     earliest_publn_year=2016, granted="N", docdb_family_size=2, nb_citing_docdb_fam=0, ipr_type="PI"))
    titles.append(dict(appln_id=aid, appln_title_lg="zh" if auth == "CN" else "en", appln_title=t))
    if a:
        abstracts.append(dict(appln_id=aid, appln_abstract_lg="en", appln_abstract=a))
    for c in codes:
        cpcs.append(dict(appln_id=aid, cpc_class_symbol=c))
SPLIT_FAMILY = fid
# a dummy application and a family outside the window
for appl_id, year, f in ((950_000_001, 2010, 99001), (aid + 1, 1995, 99002)):
    apps.append(dict(appln_id=appl_id, docdb_family_id=f, inpadoc_family_id=f, appln_auth="US", appln_kind="A ",
                     appln_filing_date=f"{year}-01-01", appln_filing_year=year, earliest_filing_year=year,
                     earliest_publn_year=year + 1, granted="N", docdb_family_size=1, nb_citing_docdb_fam=0, ipr_type="PI"))
    titles.append(dict(appln_id=appl_id, appln_title_lg="en", appln_title="Autonomous underwater vehicle"))

apps_df = pd.DataFrame(apps)
F.TABLES["TLS201_APPLN"] = apps_df
F.TABLES["TLS202_APPLN_TITLE"] = pd.DataFrame(titles)
F.TABLES["TLS203_APPLN_ABSTR"] = pd.DataFrame(abstracts)
F.TABLES["TLS224_APPLN_CPC"] = pd.DataFrame(cpcs)
F.TABLES["TLS209_APPLN_IPC"] = pd.DataFrame(ipcs)
truth = pd.DataFrame(fams)

# persons, publications, citations, legal events, fields
persons, links, pubs, cits, cats, famcit, fields, events = [], [], [], [], [], [], [], []
names = [("KONGSBERG MARITIME AS", "COMPANY", "NO"), ("Saab Seaeye Ltd", "COMPANY", "GB"),
         ("HARBIN ENGINEERING UNIVERSITY", "UNIVERSITY", "CN"), ("KABUSHIKI KAISHA TOSHIBA", "COMPANY", "JP"),
         ("Oceaneering International, Inc.", "COMPANY GOV NON-PROFIT", "US"), ("Zhang Wei", "INDIVIDUAL", "  ")]
for i, (n, sec, c) in enumerate(names):
    persons.append(dict(person_id=i + 1, person_name=n, psn_id=100 + i, psn_name=n.upper(), psn_sector=sec,
                        han_id=200 + i, han_name=None, person_ctry_code=c))
for i in range(30):
    persons.append(dict(person_id=100 + i, person_name=f"INVENTOR {i}", psn_id=500 + i, psn_name=f"INVENTOR {i}",
                        psn_sector="INDIVIDUAL", han_id=0, han_name=None, person_ctry_code=rng.choice(["CN", "US", "NO", "  "])))
pid = 0
for _, a in apps_df.iterrows():
    links.append(dict(person_id=int(rng.integers(1, len(names) + 1)), appln_id=a.appln_id, applt_seq_nr=1, invt_seq_nr=0))
    for j in range(int(rng.integers(1, 3))):
        links.append(dict(person_id=100 + int(rng.integers(0, 30)), appln_id=a.appln_id, applt_seq_nr=0, invt_seq_nr=j + 1))
    pid += 1
    pubs.append(dict(pat_publn_id=pid, appln_id=a.appln_id, publn_auth=a.appln_auth, publn_kind="A1",
                     publn_date=f"{a.appln_filing_year + 1}-06-01", publn_first_grant="N", publn_claims=int(rng.integers(5, 25))))
    if a.granted == "Y":
        pid += 1
        pubs.append(dict(pat_publn_id=pid, appln_id=a.appln_id, publn_auth=a.appln_auth, publn_kind="B1",
                         publn_date=f"{a.appln_filing_year + 3}-06-01", publn_first_grant="Y", publn_claims=10))
    events.append(dict(appln_id=a.appln_id, event_seq_nr=1, event_auth=a.appln_auth, event_code="PGFP",
                       event_publn_date="2020-01-01", event_category_title="Payment of annual fee",
                       event_text="annual fee paid", fee_country=a.appln_auth, fee_renewal_year=int(rng.integers(3, 12))))
    if rng.random() < .2:
        events.append(dict(appln_id=a.appln_id, event_seq_nr=2, event_auth=a.appln_auth, event_code="MM4A",
                           event_publn_date="2021-01-01", event_category_title="Lapse because of non-payment of due fees",
                           event_text="", fee_country=None, fee_renewal_year=None))
    fields.append(dict(appln_id=a.appln_id, techn_field_nr=int(rng.choice([32, 13, 10])), weight=1.0))
pub_df = pd.DataFrame(pubs)
for i in range(200):
    a, b = rng.choice(pub_df.pat_publn_id, 2, replace=False)
    cits.append(dict(pat_publn_id=int(a), citn_replenished=0, citn_id=i + 1, citn_origin=rng.choice(["SEA", "APP"]),
                     cited_pat_publn_id=int(b), cited_appln_id=0, cited_npl_publn_id=0 if rng.random() < .8 else 7))
    cats.append(dict(pat_publn_id=int(a), citn_replenished=0, citn_id=i + 1, citn_categ=rng.choice(["X", "Y", "A"])))
fam_ids = truth.docdb_family_id.tolist()
for i in range(150):
    a, b = rng.choice(fam_ids, 2, replace=False)
    famcit.append(dict(docdb_family_id=int(a), cited_docdb_family_id=int(b)))
F.TABLES["TLS206_PERSON"] = pd.DataFrame(persons)
F.TABLES["TLS207_PERS_APPLN"] = pd.DataFrame(links)
F.TABLES["TLS211_PAT_PUBLN"] = pub_df
F.TABLES["TLS212_CITATION"] = pd.DataFrame(cits)
F.TABLES["TLS215_CITN_CATEG"] = pd.DataFrame(cats)
F.TABLES["TLS228_DOCDB_FAM_CITN"] = pd.DataFrame(famcit)
F.TABLES["TLS230_APPLN_TECHN_FIELD"] = pd.DataFrame(fields)
F.TABLES["TLS231_INPADOC_LEGAL_EVENT"] = pd.DataFrame(events)
F.TABLES["TLS803_LEGAL_EVENT_CODE"] = pd.DataFrame([dict(event_auth="CN", event_code="PGFP", event_descr="annual fee")])
F.TABLES["TLS901_TECHN_FIELD_IPC"] = pd.DataFrame([dict(ipc_maingroup_symbol="x", techn_field_nr=32, techn_sector="Mechanical engineering", techn_field="Transport"),
                                                   dict(ipc_maingroup_symbol="y", techn_field_nr=13, techn_sector="Instruments", techn_field="Control"),
                                                   dict(ipc_maingroup_symbol="z", techn_field_nr=10, techn_sector="Instruments", techn_field="Measurement")])

# ---------------------------------------------------------------- label files
rel = truth.set_index("docdb_family_id")["relevant"]
first = apps_df.drop_duplicates("docdb_family_id").set_index("docdb_family_id")
tt = F.TABLES["TLS202_APPLN_TITLE"].set_index("appln_id")["appln_title"]


def label_file(ids, name, extra=None):
    d = pd.DataFrame({"docdb_family_id": ids})
    d["appln_id"] = d.docdb_family_id.map(first.appln_id)
    d["appln_title"] = d.appln_id.map(tt)
    d["is_relevant"] = d.docdb_family_id.map(rel).fillna(0).astype(int)
    for k, v in (extra or {}).items():
        d[k] = v
    d.to_csv(RUN / "labels" / name, index=False, encoding="utf-8-sig")


label_file(fam_ids[:40], "broad_net_test.csv", {"sample": "broad_net"})
label_file(fam_ids[30:70], "candidates_test.csv", {"sample": "candidates", "label_b": 1})
label_file(fam_ids[70:], "sector_test.csv", {"sample": "sector", "grey_zone": ""})
labs = pd.read_csv(RUN / "labels" / "sector_test.csv", encoding="utf-8-sig", dtype={"grey_zone": str})
labs.loc[labs.appln_title.str.contains("offshore wind"), "grey_zone"] = "offshore_wind"
labs.to_csv(RUN / "labels" / "sector_test.csv", index=False, encoding="utf-8-sig")

# ---------------------------------------------------------------- run the notebooks
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
plt.show = lambda *a, **k: plt.close("all")
import os  # noqa: E402
os.chdir(RUN)
sys.path.insert(0, str(RUN))
import config as C  # noqa: E402
C.FOCAL_FAMILY_IDS = tuple(fam_ids[:3])


class _Sty:
    def format(self, *a, **k):
        return self


pd.DataFrame.style = property(lambda self: _Sty())
SHOW = []


def display(x):
    SHOW.append(x)


def notebook_code(path):
    import json
    cells = json.loads(Path(path).read_text())["cells"]
    lines = []
    for c in cells:
        if c["cell_type"] == "code":
            body = "".join(c["source"])
            lines.append("\n".join(l for l in body.splitlines() if not l.lstrip().startswith(("%", "!"))))
    return "\n\n".join(lines)


def run(name):
    code = notebook_code(SRC / name)
    print(f"\n{'=' * 30} {name}")
    g = {"display": display, "__name__": "__main__"}
    exec(compile(code, name, "exec"), g)
    return g


g1 = run("01_extract.ipynb")
C.FETCH_BENCHMARK = False      # the fake client does not support sub-queries with .c (benchmarks)
g2 = run("02_clean.ipynb")
print("\nALL NOTEBOOKS RAN. Split family", SPLIT_FAMILY, "in sector:",
      bool(g1["fam"].set_index("docdb_family_id").loc[SPLIT_FAMILY, "in_sector"]))
