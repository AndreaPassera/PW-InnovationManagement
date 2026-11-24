# Team 10 · Underwater drones · Innovation Management Project Work

Politecnico di Torino, Innovation Management (2026/27). Sector scouting with patent data (PATSTAT on the EPO
Technology Intelligence Platform, TIP) for an investment bank whose client is **Fincantieri**.

## The question

Fincantieri is building an underwater business (WASS 2025; NextGeo, WSense, Graal Tech, Defcomm 2026). The question
the project answers is the one of the kick-off: **which technologies and which players of the underwater-drone sector
(AUVs, ROVs, UUVs, gliders, their parts and infrastructure) are worth investing in**. Steps 0–2, covered by this
version of the repository:

| Step | Question | Where |
|---|---|---|
| 0 | What exactly is the sector? A reproducible query, validated (precision, recall, inter-rater agreement) | `01_extract` |
| 0 | Clean data: families, applicants (harmonised names and corporate groups), dates, citations, legal events | `02_clean` |
| 1 | How big is the sector, is it growing, where, with which technologies, how is it protected, where does knowledge come from? | `03_landscape`, Section 1 |
| 2 | Who competes, how concentrated is it, who enters, which subfields grow, where are the white spaces and the risks for the client? PEST | `03_landscape`, Section 2 |

Steps 3–6 (present score, future score, quadrant, recommendation) will be added as `04_present_score`,
`05_future_score` and `06_quadrant`.

## Repository

```
team-10-underwater-drones/
├── README.md              this file: question, how to run, results
├── requirements.txt       pinned package versions
├── config/sector.yaml     every setting: environment, years, CPC blocks, samples, data switches, landscape settings
├── src/                   the pipeline as scripts (exported from the notebooks) + shared code
│   ├── config.py          reads config/sector.yaml; the sector RULES (regular expressions, exclusions, corporate groups)
│   ├── pw_utils.py        functions shared by the steps (rules, harmonisation, counting), tested in tests/
│   ├── 01_extract.py      Step 0: SQL queries run on TIP, sector definition, validation
│   ├── 02_clean.py        Step 0: families, applicants, dates, citations, legal events
│   └── 03_landscape.py    Steps 1–2
├── notebooks/             the same three steps as notebooks (the version run on TIP, with figures and comments)
├── labels/                hand labels for the validation (see labels/README.md)
├── external_data/         non-PATSTAT data with source, link and access date (see external_data/README.md)
├── tests/                 unit tests of the rules and indicators; offline run of 01–02 on a fake PATSTAT
├── tools/export_py.py     exports notebooks/ to src/
├── docs/                  method notes (sector definition, counting rules, data inventory)
└── outputs/<env>/         step0/, step1/ (data, not committed), step2/figures/ and step2/tables/
```

## How to run

**Environment.** `requirements.txt` holds the pinned versions of the TIP environment the numbers come from (recorded
by `01_extract`, Section 9.1, in `outputs/<env>/step0/environment.txt`). To rebuild the same environment on another
computer:

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The PATSTAT client (`epo.tipdata`) exists only on TIP: `01_extract` and `02_clean` query PATSTAT and therefore run
only there. Everything after them (`03_landscape`, the tests) runs on any computer with the environment above.

**On TIP** (full pipeline):

1. Upload the whole folder. Do not reinstall `requirements.txt` on TIP (it describes TIP itself); install only what
   is missing (`pip install sentence-transformers umap-learn bertopic`: Section 8 of `01_extract`, the
   encoding comparison and BERTopic of `03_landscape` §2.4.2).
2. Set `env` in `config/sector.yaml`: `TEST` for a quick check that every query runs, `PROD` for the numbers of the
   deck. Outputs go to `outputs/test/` or `outputs/prod/`, so the two runs never overwrite each other.
3. Run the notebooks in order: `notebooks/01_extract.ipynb` → `02_clean.ipynb` → `03_landscape.ipynb`.
4. Download `outputs/<env>/step0/environment.txt` (package versions) and `outputs/prod/step2/` (figures, tables,
   `key_numbers.json`); update `requirements.txt` from `environment.txt` and commit.

**Off TIP** (from the clean data): put the `outputs/prod/step1/` folder written by `02_clean` (about 80 MB, not in
git: PATSTAT extracts) in place and run `notebooks/03_landscape.ipynb` or `python src/03_landscape.py`.

**As scripts**: `python src/01_extract.py`, `python src/02_clean.py`, `python src/03_landscape.py` (from any folder
inside the repository). The scripts are exported from the notebooks with `python tools/export_py.py`.

**Tests** (no PATSTAT needed): `python -m pytest tests` (sector rules, name harmonisation, counting rules) and
`python tests/offline/run_offline.py` (every cell of 01–02 on a fake PATSTAT that accepts only real column names).

## Method in brief

- **Unit**: DOCDB family (one invention = one count), dated by its earliest priority year; patents of invention,
  2000–2025. Years after 2022 are incomplete (publication lag): shaded in every figure, excluded from every growth rate.
- **Sector**: six rule blocks on CPC codes (e.g. `B63G2008/002-008`, unmanned underwater vessels) and keywords in
  titles and abstracts, grey-zone exclusions, hand-labelled corrections; validated on random PROD samples
  (precision with Wilson intervals, recall against a broad net, Cohen's kappa). Details: `docs/step0_sector_definition.md`.
- **Counting**: fractional counting for countries, technologies and applicants; applicant names harmonised in three
  layers (normalisation, data-driven merge, verified corporate groups); three robustness samples (all, without the
  block-6-only families, international families).
- **External data**: market size reports, SIPRI military expenditure, the EU Industrial R&D Investment Scoreboard
  (company financials, used instead of Orbis), event dates and PEST sources (`external_data/README.md`). The recall
  check on the market leaders' flagship patents is computed in PATSTAT (`01_extract` §7.7), instead of Orbit.

## Results (Steps 1–2)

> Numbers from the final PROD run (23 November 2026, sector of 15,694 families); the full list is in
> `outputs/prod/step2/key_numbers.json`.

- **Size and growth.** 15,694 families (2000–2025). Families grew about 20% a year in 2012–2022, against
  5.5% for all patents and 7% for ship technology (B63); the international families grew 8.0% a year, in line with
  the market forecasts (4.2–5.1 bn USD in 2024, 8–17% a year). The excess growth is domestic Chinese filing.
- **Geography.** 73% of the families are first filed in China; 11% are international. Defence budgets go with
  international patenting (Spearman ρ 0.66 across countries), but the US, with 43% of world military spending, holds
  about a fifth of the international families: defence R&D is largely not patented. Italy sits in line with its budget
  (1.7% of the international families against 1.5% of military spending); Norway, Germany and Japan patent far more than
  they spend (figure `s2_defence_vs_patents`, bars of the two shares).
- **Life cycle.** Still in the growth phase: the logistic fit puts the inflection after the last complete year.
- **Who patents.** Universities own about 48% of the families (fractional), firms 34%; the sector is not
  concentrated (HHI 0.01, CR4 17%) and is de-concentrating; a third of the active applicants of the last five years
  are entrants.
- **Protection.** EP opposition rate 2.5% (95% CI 1.3–4.8%), in the range of the EPO average (about 2–4%); 2% of the families are
  exposed to telecom standards (SEP).
- **Knowledge flows.** 43% of the families cite science; citations are localised in the same country 1.3 times
  more than chance; 95% of the inter-firm citations show no trace of payment (pure spillovers).
- **SDGs.** 27% of the families are linked to at least one Sustainable Development Goal (SDG 14, life below water: 5%).
- **Client.** Fincantieri files few families (R&D intensity 2.3%, 1.5 families per € bn of R&D against a median of
  2.7): its underwater capabilities come from acquisitions, not internal R&D. White spaces for the client: AI and data
  processing (G06F17/, G06F18/, G06N3/), tracked vehicles (B62D55/), charging of electric vehicles (B60L53/).

## Team

Team 10 · Innovation Management, Politecnico di Torino.

## Generative AI statement

A large language model (Claude, Anthropic) was used in three ways:

- **First labels of Step 0.** The first relevance labels (`labels/labels_test.csv`, `labels/labels_prod.csv`) were
  produced by the LLM applying the written definition and the strict and broad rules of `01_extract` Section 6 to
  each title and abstract. They count as evidence only after the check against people: two team members labelled
  blind a random subset (`labels/second/`, Cohen's kappa in `01_extract` 7.2), the new samples (block 6, broad net,
  text retrieval) were labelled by hand, and every disagreement was adjudicated by the team.
- **Code.** The LLM helped write, review and test the code of the notebooks, `src/` and `tests/`. Every notebook was
  run by the team on TIP (TEST, then PROD) and the outputs were checked before being used.
- **Text.** The LLM helped draft the method notes, the readings in the notebooks and parts of the deck and appendix;
  numbers and sources were checked by the team against the outputs and the original references.

[To complete by the team: who used it for what, and anything else to declare.]

