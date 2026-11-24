# outputs/

Written by the notebooks / scripts, one folder per PATSTAT environment (`test/`, `prod/`):

| Folder | Written by | Content | In git |
|---|---|---|---|
| `<env>/step0/` | `01_extract` | final sector with its flags, raw tables, samples to label, validation tables, UMAP maps | no (PATSTAT extracts) |
| `<env>/step1/` | `02_clean` | clean datasets: families, applications, CPC, WIPO fields, citations, applicants, inventors, legal events, benchmarks | no |
| `<env>/step2/figures/` | `03_landscape` | every figure of Steps 1–2 (png) | yes (PROD) |
| `<env>/step2/tables/` | `03_landscape` | every table of Steps 1–2 (csv), for the technical appendix | yes (PROD) |
| `<env>/step2/key_numbers.json` | `03_landscape` | the headline numbers of the deck | yes (PROD) |
