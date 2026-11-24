# external_data/

Data that do not come from PATSTAT. Every row carries its **source, link and access date**; `03_landscape` reads the
files directly (a missing file skips its section with a message) and lists all sources in Section 4.

| File | Content | Source | Used in `03_landscape` |
|---|---|---|---|
| `market_size.csv` | size of the underwater-drone / UUV / AUV market (2024) and forecast CAGR, 12 estimates | commercial market reports (free summaries: IMARC, GMI, MRFR, TechSci, MarketsandMarkets, BCC, Mordor, Fortune BI, Expert MR) and Fincantieri | §1.1.2 patents vs market |
| `market_players.csv` | market leaders, flagship products, the only public market-share figure (MarkNtel, top 5 ≈ 58%) | Step 0 literature note, company websites | §1.5.1 |
| `events.csv` | dated events (policy, markets, standards, ownership), with `in_chart` and `verified` flags | official sources (CNIPA, NATO, EUR-Lex, company releases) | §1.1.1 |
| `pest_sources.csv` | one source per PEST factor | SIPRI, CNIPA, EUR-Lex, IEA, NATO, literature | §2.8 |
| `defence_spending.csv` | military expenditure by country 2000–2025 (constant 2024 USD, current USD, share of GDP) and world total | SIPRI Military Expenditure Database, 2026 release (v1.2) | §2.8.1 |
| `firm_financials.csv` | R&D, net sales, employees 2010–2024 of 46 corporate groups, with the crosswalk to the harmonised patent groups | **EU Industrial R&D Investment Scoreboard** (European Commission, JRC, panel 2003–2024 v2.0, open data), plus annual reports / 10-K for the firms it does not cover. **Used instead of Orbis (Bureau van Dijk)** | §2.2.5 |
| `ip_system_facts.csv` | EPO opposition rate, Chinese utility models and design patents | EPO Annual Reports, WIPO Patent Judicial Guide | §1.5.4, §2.2.4 |
| `group_ownership.csv` | corporate-group decisions (Seabed Geosolutions, Technip France, Cybernetix, the client's acquisitions) | company releases, SEC filings | `src/config.py` `GROUP_ALIASES` |
| `flagship_recall.csv` | known patent numbers of flagship products, checked one by one in PATSTAT | public patent lists | `01_extract` §7.7 (**used instead of Orbit**) |

Market reports are commercial estimates and disagree: the notebook reports ranges, never a single number.
