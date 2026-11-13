# Labels

Every `.csv` here is read by `01_extract.ipynb` (Section 1), with `utf-8-sig` (files saved by Excel are fine).
Rows without a 0/1 `is_relevant` are ignored. If a family is in several files, the last file (alphabetical order)
wins. Families already labelled are left out of new samples.

## How the labels are made

Each family is labelled against the written definition in `config.LABEL_DEFINITION`:

> An underwater drone is an unmanned vehicle that moves and works under water: ROVs, AUVs and UUVs, underwater
> gliders, robotic fish, seabed crawlers and hybrid vehicles that operate under water. The sector includes the drone
> itself, its parts, its infrastructure (launch and recovery, docking, charging, tethers) and the methods to control,
> navigate and coordinate it.

Each family gets **two labels**:

- `is_relevant` (**strict rule**, defines the sector and every validation number): 1 only if an underwater drone is
  the subject of the patent (the drone, a part or tool designed for a named drone, its infrastructure, or a method to
  operate it); 0 if no drone is involved, one is only mentioned in passing, an ROV only installs or operates generic
  subsea equipment, or the vehicle is not stated to be unmanned.
- `is_relevant_broad` (**broad rule**, sensitivity check only): 1 if the title and abstract match the definition
  **even partly**. Always `is_relevant` ≤ `is_relevant_broad`; if the column is missing it is set equal to
  `is_relevant`.

The grey-zone table (what is 1 under each rule) is in Section 6 of `01_extract` and in the team guide.

## Columns

| Column | Content |
|---|---|
| `docdb_family_id` | DOCDB family |
| `sample` | where the family comes from: `sector` (random sector sample), `candidates` (random sample of a wider candidate set), `block5` (families of block 5), `broad_net` (random sample of the broad net), `broad_net_outside` (random sample of the broad net outside the sector), `discovery` (top of the BM25/SBERT rankings outside the sector), `other` |
| `year`, `title`, `abstract` | what the label was based on |
| `is_relevant` | strict rule: 1 = an underwater drone is the subject of the patent, 0 = not |
| `is_relevant_broad` | broad rule: 1 = matches the definition even partly, 0 = not |
| `grey_zone` | the grey zone the family falls in: `offshore_wind`, `hybrid_surface`, `hybrid_air`, `outside_sea`, `generic_component`, `crew_not_stated`, `other` |
| `note` | the reason for the label |
| `label_b` | optional: a second, independent label for Cohen's kappa (7.2) |

## How each sample is used

| `sample` | Used for |
|---|---|
| `sector`, `candidates`, `block5`, `broad_net` (families inside the sector) | precision of the sector (7.1–7.3) |
| `broad_net`, `broad_net_outside` (families outside the sector) | recall relative to the broad net (7.4) |
| `discovery` | precision@k of BM25 and SBERT outside the sector (8.2); never in precision or recall |
| all | hand-labelled corrections of the final sector (4, 7.6) |

## Files now

`labels_test.csv`: 349 TEST families (LLM labels; used for corrections, never for validation).
`labels_prod.csv`: 259 PROD families (LLM labels): 100 `sector`, 50 `broad_net_outside`, 109 `discovery`.

## Protocol for the team (v4)

**Who.** Two labellers, A and B (two team members). A is the main labeller; B labels independently, for agreement.

**Before starting (30 min, together).** Read `config.LABEL_DEFINITION` and the grey-zone table (01_extract,
Section 6). Label together 10 families from `labels_prod.csv` that are **not** in `to_fill/kappa_blind_sheet.csv`,
discussing every doubt, and write any new convention in the grey-zone table. After this step, no more discussion until
the end.

**The rules for every family.** Read title and abstract only (the notebook files hide the block flags on purpose:
do not look at `label_samples_key.csv`, nor at the LLM labels). Then:
- `is_relevant` = 1 only if an underwater drone is the **subject** of the patent: the drone, a part or tool designed
  for one (the drone is named), its infrastructure (launch/recovery, docking, charging, tether), or a method to
  control, navigate or coordinate it. 0 if no drone, drone only mentioned, ROV that only installs generic subsea
  equipment, or vehicle not stated to be unmanned.
- `is_relevant_broad` = 1 if it matches the definition even partly (always >= `is_relevant`).
- `grey_zone`: one of `offshore_wind`, `hybrid_surface`, `hybrid_air`, `outside_sea`, `generic_component`,
  `crew_not_stated`, `other`, or empty.
- `note`: one short reason (e.g. "AUV path planning", "manned submarine", "ROV only installs a connector").
- Doubt after 2 minutes: choose, write `doubt:` in the note and move on. Time budget: about 1 minute per family.

**Task 1 · agreement with the LLM labels (now, before the re-run).** `to_fill/kappa_blind_sheet.csv` holds 60 random
families of `labels_prod.csv` (40 from the sector sample, 20 from the broad-net sample, shuffled, without the LLM
labels). A and B each copy it, fill it alone and save it as `second/<name>.csv` (e.g. `second/anna.csv`). 7.2 then
reports kappa A vs B, A vs LLM and B vs LLM.

**Task 2 · new samples (after the v4 re-run of 01_extract).** In `outputs/prod/step0/`:
1. `label_sample_block6.csv` (50 families selected by block 6 alone): **A** fills it and saves it in `labels/` as
   `block6_prod.csv`; **B** fills the first 25 rows in a copy saved as `second/<name>_block6.csv`.
2. `label_discovery.csv`: A fills only the rows with an empty `is_relevant` and saves the file as
   `labels/discovery_v4_prod.csv` (keep `sample = discovery`).
3. Optional, if time allows: `label_sample_sector.csv` (new random sector families) → `labels/sector_v4_prod.csv`,
   and `label_sample_broad_net.csv` → `labels/broad_net_v4_prod.csv`. They narrow the intervals of precision and recall.

**Adjudication.** Re-run 01_extract: 7.2 writes `outputs/prod/step0/label_disagreements.csv`. A and B discuss each
family; the agreed label goes in the **main** file (`labels_prod.csv` or `block6_prod.csv`) with `adjudicated: ...`
in `note`. The files in `second/` are never edited after they are filled (they are the evidence of the kappa).
Re-run 01_extract and 02_clean.

**What goes in the technical appendix.** Number of families labelled by humans and by the LLM, kappa with its
interpretation (Landis & Koch 1977: 0.61-0.80 substantial, > 0.80 almost perfect), the number of adjudicated
families, and the stratified precision and recall of `step0_summary.csv`.
