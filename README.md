# PW Innovation Management · Team 10 · Underwater drones

Sector scouting with patent data (PATSTAT on the EPO Technology Intelligence Platform, TIP) for an investment
bank's client, Fincantieri. This folder is **Step 0** of the pipeline: the sector definition (`01_extract`) and the clean datasets that
the later steps use (`02_clean`).

## Run order

| # | File | Input | Output |
|---|---|---|---|
| 1 | `01_extract.ipynb` | PATSTAT, `labels/` | `outputs/<env>/step0/`: final sector with its flags, raw tables, samples to label, validation, UMAP maps |
| 2 | `02_clean.ipynb` | `outputs/<env>/step0/` | `outputs/<env>/step1/`: families, CPC, WIPO fields, citations, applicants (harmonised in three layers, with audit), inventors, legal events, benchmarks |
| – | `tests/` | – | `python -m pytest tests`: unit tests of the sector rules, name harmonisation and counting rules, without PATSTAT |
| – | `tests/offline/run_offline.py` | synthetic data | runs every code cell of the two notebooks on a fake PATSTAT that accepts only real column names: run it before TIP to catch errors |

| Folder / file | Content |
|---|---|
| `config.py` | every setting: environment, years, the five blocks, the grey-zone exclusion rules, the broad net, aliases, extra sources (PW kick-off: "config, not hard-coding") |
| `pw_utils.py` | the rules shared by the notebooks, tested in `tests/` |
| `labels/` | hand labels (see `labels/README.md`): `labels_test.csv` on TEST, then the PROD samples |

## TEST and PROD

`ENV` in `config.py` chooses the PATSTAT database:

- **TEST**: a small extract (about 10K publications). Queries run in seconds, so it is for writing and debugging code.
  Its counts are **not** the sector's size and never go in the deck.
- **PROD**: the full PATSTAT (more than 140M publications). Slower. All the numbers in the deck come from here.

Outputs go to `outputs/test/` or `outputs/prod/`, so the two runs never overwrite each other.
Labels drawn on TEST describe TEST families: on PROD, draw a new sample (Section 7 of `01_extract`) and label it.

## First run on TIP (TEST)

1. `pip install -r requirements.txt`, then run `01_extract` with `ENV = "TEST"`.
2. Read Section 10 (summary): sector size, precision, recall and the checks still open.
3. Run `02_clean` on TEST and read its quality checks and `applicants_audit.csv`.
4. Switch to `ENV = "PROD"`, run `01_extract`, label the new samples, run `01_extract` and `02_clean` again.

## The sector

A DOCDB family (patents of invention, earliest priority 2000–2025) is selected if it matches any block:

1. CPC `B63G2008/002, 004, 005, 007, 008` (unmanned underwater vessels) **and** an underwater term in title or abstract;
2. a specific keyword (e.g. *autonomous underwater*, *underwater glider*, *robotic fish*) in title or abstract;
3. a generic keyword (*remotely operated vehicle*, *underwater robot*, ...) in the title, or in the abstract of a family with a `B63` or `B25J` code;
4. CPC `B63G8/` or `B63C11/` **and** a robot term (*robot*, *drone*, *ROV*, *AUV*, *UUV*, ...);
5. *ROV*, *AUV* or *UUV* as a whole word in the title.

**Grey zones.** Selected families whose title is about an energy device or offshore structure (turbine, generator,
energy supply, wind farm, propeller...) **and** has no vehicle term (robot, ROV, AUV, unmanned, vehicle, inspection,
monitoring...) are removed (`config.EXCLUSION_RULES`). Offshore-wind inspection drones and surface or aerial systems
that carry an underwater vehicle stay in. On the 349 labelled TEST families the rule fires on 20, 2 of them
relevant (the hand-labelled corrections put those back).

**Hand-labelled corrections** (`USE_LABEL_CORRECTIONS`): families labelled 0 leave the sector, families labelled 1
that the rules miss enter it. Precision and recall are measured on the sector given by the rules, before the
corrections (measuring the corrected sector on the same labels would be circular).

**Matching level** (`MATCH_LEVEL`): `family` (default) pools the texts and codes of all the family's applications,
which recovers inventions whose English text sits on another member of the family (Tutorial 1); `application`
requires code and words on the same application. Both are computed (`in_sector_family`, `in_sector_application`).

**Why CPC and not IPC.** The codes for unmanned underwater vessels (`B63G2008/...`) exist only in the CPC, and the
CPC contains the IPC main groups in more detail. Families without any CPC code can enter only through keywords:
Section 4.2 of `01_extract` measures how many there are, by office.

**Validation** (Section 7): precision with Wilson intervals (by block, period and office), Cohen's kappa, the check
of the exclusion rules, grey-zone sensitivity, recall relative to a **broad net** (a deliberately wide query with IPC
codes and generic phrases such as *underwater vehicle*: a random sample of its families outside the sector estimates
what the sector misses) and the false positives by block.

## Counting rules (state them in the technical appendix)

Same rules as Tutorial 0, Notebook 2, section 4. They apply to every number from Step 1 on.

| Rule | What it says | Where it is implemented |
|---|---|---|
| Unit | DOCDB family (one invention = one count); the sector is selected at family level, then all its applications can be counted | `families.csv` (family), `applications.csv` (application twin) |
| Family year | smallest `earliest_filing_year` of the family's applications (first filing anywhere) | `01_extract`, Section 3 → `family_year` |
| Truncation | years after `LAST_COMPLETE_YEAR` (2022) are incomplete (18-month publication lag): shaded in every figure, excluded from growth | `is_truncated`, `is_truncated_appln`; `pw_utils.shade_truncated`, `growth_rate`, `cagr` |
| PCT | `appln_auth = 'WO'` and `appln_kind` starting with `W`; a family is "via PCT" if it has one | `applications.is_pct`, `families.via_pct` |
| Fractional counting | a unit is split equally among its distinct persons with a known country (2 IT + 1 DE → 2/3, 1/3), or among its CPC subclasses (4 subclasses → 1/4 each); each unit sums to 1 | `pw_utils.fractional_weights`; `country_weight` in `applicants.csv` / `inventors.csv`; `*_countries_by_application.csv`; `family_subclass.csv`, `application_subclass.csv`; WIPO fields |
| Actor shares | each family split equally among its applicant groups (for HHI and top players) | `applicants.weight` |

Two documented differences from the Tutorial. Countries are split among distinct applicant **groups** at family level
(after harmonisation, so one firm with two `person_id`s is not counted twice); at application level among distinct
persons, as in the Tutorial. `family_subclass.weight` uses all subclasses as in the Tutorial, and `weight_tech`
leaves out the `Y` tagging section.

## Data inventory

What is collected, from which table, and which course concept it serves.

| Information | PATSTAT source | Where | Used for |
|---|---|---|---|
| Families, priority year, offices, grant, PCT, family size, INPADOC family | `tls201` | `families.csv` | Step 1 volume, geography; Step 3 success, globalization |
| Titles, abstracts (English for NLP only) | `tls202`, `tls203` | `families.csv` | query, topics, text novelty |
| CPC codes | `tls224` | `family_cpc.csv` | technology mix, subfields, novel recombination |
| WIPO 35 fields / 5 sectors | `tls230`, `tls901` | `family_wipo_fields.csv` | Step 1 technology mix (Schmoch, 2008) |
| Grant date, grant lag, number of claims | `tls211` | `families.csv` | quality indicators (Squicciarini et al., 2013) |
| Family citations, 5-year window, inside-sector flows | `tls228` | `citations_*.csv` | influence (Hall et al., 2001), spillovers |
| Citation origin, X/Y/A categories, NPL (science) citations | `tls212`, `tls215` | `citation_categories.csv`, `families.csv` | blocking prior art, science linkage, future score |
| CPC of cited and citing families | `tls224` | `cited_citing_cpc.csv` | originality, generality (Trajtenberg et al., 1997) |
| Applicants: PSN/HAN names harmonised in three layers (normalisation, data-driven anchor merge, verified groups), type, country (filing-office fallback), weights, entry year, client flag | `tls206`, `tls207` | `applicants.csv`, `applicants_audit.csv`, `applicants_profile.csv` | Step 2 HHI, entry, top players; NPEs (universities) |
| Co-assignments, firm–university collaboration | `tls206`, `tls207` | `families.csv` | partners, Step 6 |
| Inventors: country, team size | `tls206`, `tls207` | `inventors.csv` | where R&D happens vs where owners are |
| Legal events: renewals, lapses, oppositions, transfers, licences; active-family proxy | `tls231`, `tls803` | `legal_events.csv.gz`, `families.csv` | value (renewals, opposition), ownership changes, risks |
| Families per year: all patents and neighbouring field (`B63`) | `tls201`, `tls224` | `benchmark_families_per_year.csv` | Step 1 growth vs total, patent propensity |
| Utility models with the same query | `tls201.ipr_type = 'UM'` | `outputs/<env>/step0_UM/` | robustness, appropriability (IM_Theory_0) |

**Not implemented here (possible extensions mentioned in the lectures):**

- *EP Register* (`reg*` tables, Tutorial 0): EP opposition outcomes, licensees, validation states. It refines
  `ev_opposition` and `ev_licence` for EP filings.
- *EP full text* (EPAB, Tutorial 2): claims and descriptions for text-based novelty.
- *OECD patent quality indicators* and *REGPAT*, the *Marx & Fuegi* patent-to-paper dataset, *Orbit* (available at
  PoliTO), and firm financials (IM_Theory_3). These are external sources, to be merged on publication number or applicant.

## Labelling workflow

1. Run `01_extract.ipynb` up to Section 6. It writes the files to label, **without the flags** that selected each
   family (they are in `label_samples_key.csv`, so labellers are not anchored): `label_sample_sector.csv` (precision)
   and `label_sample_broad_net.csv` (recall). Section 8.2 writes `label_discovery.csv` (the families that BM25 and
   Patent SBERT rank highest outside the sector).
2. Label each family against the written definition (`config.LABEL_DEFINITION`): two labels per family,
   `is_relevant` (strict rule: 1 only if an underwater drone is the subject of the patent; it defines the sector) and
   `is_relevant_broad` (broad rule: 1 if it matches even partly; sensitivity check only); write the reason in `note`
   and the grey zone in `grey_zone`. Rule and
   grey zones: Section 6 of the notebook. Optionally, a second person fills `label_b` on some families for kappa (7.2).
3. Save the files in `labels/` and re-run: validation (Section 7), corrections and final sector (Section 4, 7.6).

## Lexical vs semantic retrieval

- The **sector definition** uses rules (CPC + keywords): transparent, reproducible, precision measurable per block.
- **BM25** (Tutorial 0), **TF-IDF** and **Patent SBERT** (`graziasveva93/sbert-adapt-ub`, from Hugging Face) are used
  as checks, with five typical sector patents and a written sector description as queries, in two experiments:
  inside the sector (8.1, paired McNemar tests) and outside it (8.2, what the rules miss). Section 8.3 draws **UMAP
  maps** of the Patent SBERT space (as in Tutorial 0): sector vs excluded families, BM25 vs SBERT top results, the
  sector by block (`umap_*.png`). BM25 is implemented in `pw_utils.py` (Okapi, k1 = 1.5, b = 0.75).

## Setup

On TIP, `epo.tipdata` is preinstalled. Install the rest with

```
pip install -r requirements.txt
```

Before submission, pin the exact versions used on TIP with `pip freeze > requirements.txt`, as the kick-off
asks for pinned versions.

## Submission checklist (PW kick-off)

- [ ] Final run on PROD, new samples labelled
- [ ] Bulk extracts not committed (`outputs/` is git-ignored); the queries are committed
- [ ] Repository shared with the teaching team; tag `v1.0-submission`, with its commit hash on the deck's title slide
- [ ] Contribution statement and generative-AI statement in the deck

## Generative-AI statement

Claude (Anthropic) helped design the sector rules and write these notebooks. The hand labels in `labels/` were
produced with Claude, which applied the written definition of underwater drones (`config.LABEL_DEFINITION`) and the
two rules (strict `is_relevant`, which defines the sector, and broad `is_relevant_broad`, "1 if it matches even
partly", kept as a sensitivity check) to each title and abstract; the reason of every label is in the `note` column.

## Results committed to the repository

The notebooks are committed with their PROD outputs. Bulk PATSTAT extracts are not committed (kick-off rule: commit the
queries, not the data): `outputs/<env>/` is git-ignored and is rebuilt by running the notebooks on TIP. Only small
summary files used in the technical appendix are kept:

- `outputs/figures/step0/`: block overlap and the three UMAP maps (PROD);
- `outputs/tables/step0/`: `step0_summary.csv` (key numbers), `retrieval_evaluation.csv`, `discovery_precision_at_k.csv`;
- `outputs/tables/step1/`: yearly benchmark counts (all patents and B63) used for the growth comparison.
