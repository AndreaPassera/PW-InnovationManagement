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

`labels_test.csv`: 349 TEST families, 153 relevant under the strict rule and 186 under the broad rule (the 33 in
between are grey-zone families), with `grey_zone` and `note` filled.
On PROD save the labelled samples as new files (e.g. `sector_prod.csv`, `broad_net_prod.csv`, `discovery_prod.csv`):
labels drawn on TEST describe TEST families.
