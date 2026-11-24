"""
Shared configuration for the Step 0 pipeline (01_extract -> 02_clean). Version 5 (v4: block 6, PROD-only validation,
legal-event flags on the tls803 descriptions; v7: applicant type from the name when psn_sector is empty, Deepinfar merged; v5: water / marine context for block 3, more corporate groups).

Every notebook imports from here. The settings (environment, years, CPC blocks, sample sizes, data switches) are
read from config/sector.yaml; the rules (regular expressions, exclusions, corporate groups) are below.
"""
import os
from pathlib import Path

# ----------------------------------------------------------------------------- repository and settings file
# ROOT = the repository (team-10-underwater-drones/). Every path below hangs from it, so notebooks (notebooks/),
# scripts (src/) and tests can run from any folder. PW_ROOT overrides it (used by the offline test).
ROOT = Path(os.environ.get("PW_ROOT", Path(__file__).resolve().parents[1])).resolve()
SETTINGS_FILE = ROOT / "config" / "sector.yaml"


def _load_settings():
    """config/sector.yaml as a dict; {} (defaults below) if the file or PyYAML is missing."""
    try:
        import yaml
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:      # noqa: BLE001
        print(f"config: {SETTINGS_FILE} not read ({type(e).__name__}); using the defaults in src/config.py")
        return {}


_Y = _load_settings()


def _get(section, key, default):
    return (_Y.get(section) or {}).get(key, default)


# ----------------------------------------------------------------------------- environment
ENV = os.environ.get("PW_ENV", _get("environment", "env", "PROD")).upper()   # TEST while developing, PROD for the deck
SECTOR_NAME = _get("sector", "name", "Underwater drones")
CLIENT = _get("sector", "client", "FINCANTIERI")

# ----------------------------------------------------------------------------- counting rules
# Patents of invention ("PI") are the PW unit. Utility models ("UM", IM_Theory_0: cheaper, no inventive-step check,
# very common in China) can be counted separately with the SAME query, as a robustness / appropriability check:
# in the first cell of 01_extract run  %env PW_IPR_TYPE=UM  before importing config. Outputs then go to step0_UM/.
IPR_TYPE = os.environ.get("PW_IPR_TYPE", _get("environment", "ipr_type", "PI"))
YEAR_MIN, YEAR_MAX = int(_get("years", "min", 2000)), int(_get("years", "max", 2025))
MISSING_YEAR = 9999     # PATSTAT code for an unknown year
TRUNCATION_WINDOW = int(_get("years", "truncation_window", 3))   # last years incomplete because of the 18-month publication lag
LAST_COMPLETE_YEAR = YEAR_MAX - TRUNCATION_WINDOW   # = 2022: years after it are flagged, not read as a decline

# Family year = MIN(earliest_filing_year) over the PI applications of the family
# (earliest priority of the invention), computed in 01_extract and reused everywhere.

# ----------------------------------------------------------------------------- sector query (final version)
# Block 1 - CPC 2000-series indexing codes for unmanned underwater vessels ...
# B63G2008/002 unmanned underwater vessels, /004 autonomously operating (AUV), /005 remotely controlled (ROV),
# /007 tethered, /008 docking stations for unmanned underwater vessels (values in config/sector.yaml)
BLOCK1_CPC = tuple(_get("query", "block1_cpc", ["B63G2008/002", "B63G2008/004", "B63G2008/005", "B63G2008/007",
                                                 "B63G2008/008"]))
# ... AND an underwater term in title or abstract (regex, matched on lower-case text).
# Acronyms are matched as whole words (optional plural), so "rov" never matches "improve".
UNDERWATER_TERMS = (
    r"\bunderwater", r"\bsub[\s-]?sea", r"\bundersea", r"\bsubmersible", r"\bsubmarine",
    r"\bseabed", r"\bsea[\s-]?floor",
    r"\brovs?\b", r"\bauvs?\b", r"\buuvs?\b",
)

# Block 2 - specific keywords, anywhere in title or abstract. "unmanned / uncrewed / autonomous underwater" has no
# "vehicle" on purpose: it also catches "... underwater robot / glider / system".
SPECIFIC_KEYWORDS = (
    r"\b(?:unmanned|uncrewed|autonomous)[\s-]+underwater",
    r"\b(?:unmanned|uncrewed|autonomous)[\s-]+submersible",
    r"\bunderwater[\s-]+drone", r"\bsubsea[\s-]+drone",
    r"\bunderwater[\s-]+glider",
    r"\bsubsea[\s-]+resident[\s-]+vehicle",
    r"\bbenthic[\s-]+vehicle",
    r"\b(?:underwater|seabed)[\s-]+crawler",
    r"\brobotic[\s-]+fish",
    r"\bbiomimetic[\s-]+underwater[\s-]+robot",
    r"\bswarming[\s-]+underwater",
    r"\bmulti[\s-]?auvs?\b",
)

# Block 3 - generic keywords: in the TITLE, or in the abstract of a family carrying a B63 or B25J code
GENERIC_KEYWORDS = (
    r"\bremotely[\s-]+operated[\s-]+vehicle",
    r"\bremotely[\s-]+operated[\s-]+underwater",
    r"\b(?:underwater|subsea|submarine)[\s-]+robot",
)
BLOCK3_CPC = tuple(_get("query", "block3_cpc", ["B63", "B25J"]))
# v5: block 3 also needs a water / marine context (a term below anywhere in the texts, or a B63 code). Without it,
# "remotely operated vehicle" also catches warehouse robots (AutoStore names its grid robots so: 26 families) and
# other land vehicles: about 60 block-3-only families had no water or sea term at all (checked on PROD, Nov 2026).
BLOCK3_CONTEXT_TERMS = UNDERWATER_TERMS + (
    r"\bwater", r"\bsea\b", r"\bseas\b", r"\bmarine", r"\bocean", r"\boffshore", r"\bvessels?\b", r"\bships?\b",
    r"\bhull", r"\bnaval", r"\bdiv(?:er|ers|ing)\b", r"\bharbou?rs?\b", r"\blake", r"\briver", r"\breservoir", r"\bdam\b",
)
BLOCK3_CONTEXT_CPC = tuple(_get("query", "block3_context_cpc", ["B63"]))

# Block 4 - neighbouring main groups AND a robot term in title or abstract
BLOCK4_CPC = tuple(_get("query", "block4_cpc", ["B63G8/", "B63C11/"]))
ROBOT_TERMS = (
    r"\brobot", r"\bdrones?\b", r"\bremotely[\s-]+operated",
    r"\brovs?\b", r"\bauvs?\b", r"\buuvs?\b",
)
# Tested and rejected after the PROD run (v6): adding "unmanned / uncrewed" (and "autonomous" with B63G8/) brought in
# 350 families, mostly unmanned surface boats, aerial drones and crewed submersibles (about 40% relevant on a read
# of 45 titles), and moved the known-item recall only from 32.5% to 33.3%. The leaders' families it misses are mostly
# crewed submarines, torpedoes and sonar antennas (01_extract 7.7), so the robot terms stay as in v5.

# Block 6 - added after the PROD validation (recall): a generic object ("underwater vehicle", "underwater X robot",
# glider) together with an operating task, or an underwater robot / glider on its own. On the labelled families
# OUTSIDE the v3 rule sector it recovers 62 of 93 relevant ones and catches 21 of 262 not relevant ones (those
# labels are not a random sample): its precision is measured on its own random sample (label_sample_block6.csv).
BLOCK6_OBJECT = (
    r"\bunderwater[\s-]+(?:\w+[\s-]+){0,2}vehicles?\b",
    r"\buninhabited[\s-]+underwater",
)
BLOCK6_TASK = (
    r"\bpath", r"\broute", r"trajector", r"obstacle", r"navigat", r"positioning", r"depth[\s-]+control",
    r"\bhover", r"docking", r"\brecover", r"formation", r"autonom", r"unmanned", r"uninhabited", r"uncrewed", r"\bswim",
)
BLOCK6_STANDALONE = (   # enough on their own, no task needed
    r"\b(?:underwater|subsea|submarine)[\s-]+(?:\w+[\s-]+){0,2}robots?\b",
    r"\bunderwater[\s-]+(?:\w+[\s-]+)?glider", r"\bglider[\s-]+robot",
)
BLOCK6_SQL_PHRASES = ("underwater vehicle", "underwater%vehicle", "underwater%robot", "subsea%robot",
                      "submarine%robot", "uninhabited underwater", "glider")

# Matching level of the blocks.
# "application": a block fires when ONE application has both the code and the words in its own title/abstract;
# "family":      codes and texts of all the applications of the family are pooled (Tutorial 1, Notebook 10, 5:
#                a family-level keyword search recovers inventions first filed in other languages).
# Both are always computed (columns in_sector_application / in_sector_family); this one defines the sector.
MATCH_LEVEL = _get("query", "match_level", "family")

# Block 5 - ROV / AUV / UUV as a whole word in the TITLE (an acronym in the title names the object of the patent).
BLOCK5_TITLE_ACRONYMS = (r"\brovs?\b", r"\bauvs?\b", r"\buuvs?\b")
# SQL LIKE cannot match whole words: these patterns keep the candidate pool small ("% rov%" still catches "rover",
# which the Python regex then drops); applied to titles only
BLOCK5_SQL_PATTERNS = ("% rov%", "rov %", "%(rov%", "%-rov%", "% auv%", "auv %", "%(auv%", "%-auv%",
                       "% uuv%", "uuv %", "%(uuv%", "%-uuv%")

# ----------------------------------------------------------------------------- grey zones
# The labelling rule (01_extract, Section 6) says how each grey zone is labelled. In the sector:
# - families that must stay in (offshore-wind inspection drones, surface or aerial systems that carry an underwater
#   vehicle) are kept by the blocks;
# - families that must go out are removed by the EXCLUSION RULES below, applied to the TITLE (the titles of all the
#   family's applications). A family is excluded when its title matches an exclusion pattern AND contains no
#   vehicle term (KEEP_TERMS). Both rules were checked on the labelled families (01_extract, 7.3).
EXCLUSION_RULES = {
    # energy devices and offshore-wind / marine-energy structures, where an ROV or a submarine is only mentioned
    "energy_device": r"turbine|power[\s-]+generat|generator|hydrokinetic|wave[\s-]+(?:energy|power|rotor)"
                     r"|wind[\s-]+(?:power|energy|turbine|farm)|energy[\s-]+(?:supply|transfer|conver)|power[\s-]+plant"
                     r"|hydrogen|transmission[\s-]+of[\s-]+power|propeller",
}
KEEP_TERMS = (r"\brobot", r"\brovs?\b", r"\bauvs?\b", r"\buuvs?\b", r"\bdrones?\b", r"\bunmanned", r"\buncrewed",
              r"\bautonomous", r"\bremotely", r"\bglider", r"robotic", r"\bcrawler", r"\bvehicle",
              r"\binspect", r"\bmonitor", r"\bdetect")

# Topic flags, only to COUNT how many sector families touch each grey zone (4.3) and to read them;
# they never change the sector.
GREY_ZONE_PATTERNS = {
    "offshore_wind": r"\boffshore[\s-]+wind|\bwind[\s-]+(?:turbine|farm|power|park)|\bmonopile",
    "hybrid_surface": r"\b(?:unmanned|autonomous|uncrewed)[\s-]+(?:surface|ship|boat)|\busvs?\b|\bsurface[\s-]+(?:vehicle|vessel|drone)"
                      r"|\bsemi[\s-]?submersible|\bmother[\s-]?ship",
    "hybrid_air": r"\baerial|\buavs?\b|\baircraft|\bflying|\bamphibious|\bair[\s-]+(?:and[\s-]+)?(?:water|sea)|"
                  r"\bcross[\s-]?(?:domain|medium)|\btrans[\s-]?medi",
    "outside_sea": r"\bnuclear|\breactor|\bpipe[\s-]?(?:line)?[\s-]+inspection|\bin[\s-]?pipe|\bsewer|\btank[\s-]+(?:cleaning|inspection)"
                   r"|storage[\s-]+and[\s-]+retrieval|\bwarehouse",
}
OFFSHORE_WIND_CPC = ("Y02E10/727", "F03D13/25", "E02B2017/0091")
GREY_ZONE_VALUES = ("offshore_wind", "hybrid_surface", "hybrid_air", "outside_sea", "generic_component",
                    "crew_not_stated", "other")

# Hand-labelled corrections (01_extract, 7.6): families labelled 0 leave the sector, families labelled 1 that the
# query misses (broad-net and discovery samples) enter it.
USE_LABEL_CORRECTIONS = bool(_get("query", "use_label_corrections", True))

# The definition every family is labelled against (01_extract, Section 6). Two labels per family:
#   is_relevant (STRICT rule, defines the sector): 1 only if an underwater drone, a part or tool designed for one, its
#     infrastructure or a method to operate it is the subject of the patent; 0 if the drone is only mentioned, only
#     installs or operates generic subsea equipment, or the vehicle is not stated to be unmanned.
#   is_relevant_broad (BROAD rule, sensitivity check only): 1 if the patent matches the definition even partly.
# is_relevant <= is_relevant_broad always.
LABEL_DEFINITION = """
An underwater drone is an unmanned vehicle that moves and works under water: remotely operated vehicles (ROVs),
autonomous and unmanned underwater vehicles (AUVs, UUVs), underwater gliders, bio-inspired robots (robotic fish),
seabed crawlers and hybrid vehicles that operate under water. The sector includes the drone itself, its parts
(hull, propulsion, buoyancy, power, sensors, manipulators, tools carried or operated by it), its infrastructure
(launch and recovery, docking, charging, tethers) and the methods to control, navigate and coordinate it.
"""

# ----------------------------------------------------------------------------- broad net (recall check)
# A deliberately wide query (IPC + CPC + generic keywords): families it finds and the sector does not are where the
# sector could miss relevant patents. A random sample of them is labelled to estimate recall (01_extract, 7.4).
BROAD_IPC = ("B63G8/00", "B63G8/08", "B63G8/14", "G01S15/89", "H04B11/00", "G01C13/00")
BROAD_CPC = BLOCK1_CPC
BROAD_KEYWORDS = (
    "underwater drone", "subsea drone", "underwater vehicle", "unmanned underwater vehicle",
    "uncrewed underwater vehicle", "autonomous underwater vehicle", "remotely operated vehicle",
    "remotely operated underwater", "unmanned underwater", "autonomous underwater",
    "uncrewed underwater", "unmanned submersible", "autonomous submersible", "uncrewed submersible",
    "underwater glider", "subsea robot", "underwater robot", "submarine robot",
    "subsea resident vehicle", "benthic vehicle", "hovering autonomous underwater",
    "underwater crawler", "seabed crawler", "biomimetic underwater robot", "robotic fish",
    "swarming underwater", "multi-auv",
)

# ----------------------------------------------------------------------------- retrieval experiments (01_extract, 8)
FOCAL_FAMILY_IDS = (55819443, 78422041, 74782746, 59856089, 58166573)   # five typical sector patents
SECTOR_DESCRIPTION = """
Technology concerning unmanned and uncrewed underwater vehicles,
including autonomous underwater vehicles (AUVs) and remotely operated
vehicles (ROVs). These systems are designed to operate underwater
without a human operator on board and may perform autonomous or
remotely controlled navigation, exploration, inspection, monitoring,
mapping and maintenance activities.

The technology includes underwater propulsion, guidance and control,
navigation and positioning systems, obstacle avoidance, underwater
communication, sensors, robotic manipulation and docking systems.
Applications include subsea infrastructure inspection, oceanographic
research, offshore operations, seabed exploration and underwater
intervention.
"""

# ----------------------------------------------------------------------------- sampling and labels
SEED = int(_get("validation", "seed", 42))
SAMPLE_SIZE = int(_get("validation", "sample_size", 100))   # random in-sector families to hand-label (PW kick-off: ~100)
BROAD_SAMPLE_SIZE = int(_get("validation", "broad_sample_size", 50))   # broad-net families outside the sector, labelled to estimate recall
LABEL_DIR = ROOT / "labels"   # label files go here (see labels/README.md)
LABEL_COL = "is_relevant"    # label column (0/1)
LABEL_COL_BROAD = "is_relevant_broad"   # broad-rule label (0/1), upper bound of precision
SECOND_LABEL_DIR = LABEL_DIR / "second"  # independent second labels, only for agreement (kappa); see labels/README.md
BLOCK6_SAMPLE_SIZE = int(_get("validation", "block6_sample_size", 50))   # random families selected by block 6 ALONE, labelled as their own stratum (01_extract, 7.1)
# Validation (01_extract, 7.1 and 7.4) uses only RANDOM PROD samples. TEST labels describe another database and come
# from non-random samples (candidates, block5): they still correct the sector, but never enter precision or recall.
PRECISION_SAMPLES = tuple(_get("validation", "precision_samples", ["sector", "block6"]))
VALIDATION_EXCLUDE_FILES = tuple(_get("validation", "validation_exclude_files", ["labels_test.csv"]))
INVALID_APPLN_ID = 900_000_000      # appln_id 0 or above this = dummy application (Tutorial 1, Notebook 10, 3.1)

# ----------------------------------------------------------------------------- I/O
_SUFFIX = "" if IPR_TYPE == "PI" else f"_{IPR_TYPE}"
OUT_STEP0 = ROOT / "outputs" / ENV.lower() / f"step0{_SUFFIX}"
OUT_STEP1 = ROOT / "outputs" / ENV.lower() / f"step1{_SUFFIX}"   # cleaned datasets used from Step 1 on
OUT_STEP2 = ROOT / "outputs" / ENV.lower() / f"step2{_SUFFIX}"   # 03_landscape: figures/ and tables/
EXT_DIR = ROOT / "external_data"   # non-PATSTAT data (market, defence spending, financials, events, sources)
LANDSCAPE = dict(_Y.get("landscape") or {})   # 03_landscape settings (main sample, split year, ...)
CHUNK = int(_get("extraction", "chunk", 5000))   # ids per query when filtering with IN (...), keeps PROD queries manageable

SBERT_MODEL = _get("extraction", "sbert_model", "graziasveva93/sbert-adapt-ub")   # Patent SBERT used in the course tutorials

# ----------------------------------------------------------------------------- 02_clean: extra data sources
# Each block can be switched off if it is too slow on PROD. See README, "Data inventory".
FETCH_PUBLICATIONS = bool(_get("extraction", "fetch_publications", True))    # tls211: grant date and grant lag, number of claims (OECD quality indicators)
FETCH_CITATION_DETAIL = bool(_get("extraction", "fetch_citation_detail", True)) # tls212 + tls215: citation origin, X/Y/A categories, NPL (science) citations
FETCH_CITATION_CPC = bool(_get("extraction", "fetch_citation_cpc", True))    # CPC subclasses of cited / citing families (originality, generality)
FETCH_INVENTORS = bool(_get("extraction", "fetch_inventors", True))       # tls207/tls206 inventors: where R&D happens, team size
FETCH_LEGAL_EVENTS = bool(_get("extraction", "fetch_legal_events", True))    # tls231 INPADOC legal events: renewals, lapses, oppositions, transfers
FETCH_WIPO_FIELDS = bool(_get("extraction", "fetch_wipo_fields", True))     # tls230 + tls901: WIPO 35 technology fields (Schmoch 2008)
FETCH_BENCHMARK = bool(_get("extraction", "fetch_benchmark", True))       # families per year in all patents and in a neighbouring field (growth vs total)
BENCHMARK_CPC = tuple(_get("query", "benchmark_cpc", ["B63"]))   # neighbouring field: ships and marine technology (patent-propensity comparison)

# ----------------------------------------------------------------------------- applicant harmonisation
LEGAL_SUFFIXES = (
    "CO LTD", "CO LIMITED", "COMPANY LIMITED", "COMPANY", "CORPORATION", "CORP", "INCORPORATED", "INC",
    "LIMITED", "LTD", "LLC", "PLC", "GMBH", "AG", "SPA", "SRL", "SAS", "SA", "AS", "ASA", "AB", "OY", "BV",
    "NV", "KK", "KABUSHIKI KAISHA", "CO", "GROUP CO", "HOLDINGS", "HOLDING",
)
LEGAL_PREFIXES = ("KABUSHIKI KAISHA", "THE")   # legal forms written BEFORE the name ("KABUSHIKI KAISHA TOSHIBA")

# regex on the normalised name -> corporate group. Starting list: extend it after reading
# outputs/<env>/step1/applicants_to_review.csv and document every rule in the technical appendix.
GROUP_ALIASES = {
    r"^KONGSBERG": "KONGSBERG",
    r"^SAAB": "SAAB",
    r"^OCEANEERING": "OCEANEERING",
    r"^TELEDYNE": "TELEDYNE",
    r"^SUBSEA ?7": "SUBSEA 7",
    # Seabed Geosolutions: Fugro (60%) - CGG (40%) joint venture from 2013, Fugro sole owner since 30 Dec 2019
    # (Fugro press release, 30.12.2019): grouped with its controlling owner at filing time
    r"^FUGRO|^SEABED GEOSOLUTIONS": "FUGRO",
    r"^HUNTINGTON INGALLS|^HYDROID": "HUNTINGTON INGALLS (HYDROID)",
    r"^GENERAL DYNAMICS|^BLUEFIN ROBOTICS": "GENERAL DYNAMICS (BLUEFIN)",
    r"^BOEING": "BOEING",
    r"^LOCKHEED MARTIN": "LOCKHEED MARTIN",
    r"^C?THALES": "THALES",                       # "CTHALES" = a PATSTAT typo
    # Atlas Elektronik is wholly owned by thyssenkrupp Marine Systems (TKMS) since 2017
    # thyssenkrupp AG files together with its marine unit (TKMS, listed in Oct 2025 with thyssenkrupp keeping 51%):
    # "THYSSEN KRUPP" families are co-assigned with TKMS, so the parent is grouped with it; misspelt Atlas names too
    r"ATLAS ELE[CK]?TRONI[KC]|^THYSSEN ?KRUPP|^TKMS": "TKMS (ATLAS ELEKTRONIK)",
    r"^NAVAL GROUP|^DCN[SX]?$|^DCN[SX]? |^DIRECTION DES CONSTRUCTIONS NAVALES": "NAVAL GROUP",   # DCN, DCNS, DCNX   # DCNS renamed Naval Group in 2017
    # Hanwha: Daewoo Shipbuilding & Marine Engineering became Hanwha Ocean (2023); Samsung Thales became Hanwha Systems (2015)
    r"^HANWHA|^DAEWOO SHIPBUILDING|^SAMSUNG THALES": "HANWHA",
    # Technip France: part of TechnipFMC from the 2017 merger; the subsea business stayed in TechnipFMC after the
    # Feb 2021 spin-off of Technip Energies (which took Cybernetix, kept separate). Confirm current assignee in Orbit.
    r"^FMC TECHNOLOGIES|^TECHNIPFMC|^SCHILLING ROBOTICS|^TECHNIP FRANCE": "TECHNIPFMC",
    r"^L3 ?HARRIS|^L 3 HARRIS|^L3 TECHNOLOGIES|^HARRIS CORP|^HARRIS$": "L3HARRIS",   # "HARRIS CORPORATION" normalises to HARRIS
    r"^FORUM ENERGY": "FORUM ENERGY TECHNOLOGIES",
    r"^ECA ROBOTICS|^ECA GROUP|^GROUPE GORGE|^EXAIL|^IXBLUE": "EXAIL (ECA)",   # Exail = ECA Group + iXblue (merged in 2022)
    # CLIENT. Since January 2025 Fincantieri owns Leonardo's underwater business (UAS, ex Whitehead Alenia Sistemi
    # Subacquei, now WASS Submarine Systems); in 2026 it bought Graal Tech (AUV/ROV), WSense (underwater comms),
    # NextGeo (marine survey) and Defcomm (USVs). Every Leonardo family in this sector is an underwater one (checked
    # in v4: 3 families), so Leonardo is grouped with the client; re-check this if the sector changes.
    r"^FINCANTIERI|^WASS SUBMARINE|^WHITEHEAD|^LEONARDO|^FINMECCANICA|^GRAAL TECH|^WSENSE|^NEXT ?GEOSOLUTIONS"
    r"|^DEFCOMM": "FINCANTIERI",
    r"^SAIPEM": "SAIPEM",
    r"^HOUSTON MECHATRONICS|^NAUTICUS ROBOTICS": "NAUTICUS ROBOTICS",
    r"^SHELL|^ROYAL DUTCH SHELL": "SHELL",
    r"^EQUINOR|^STATOIL": "EQUINOR",
    # CNOOC and its listed engineering subsidiary Offshore Oil Engineering Co. (COOEC)
    r"^CNOOC|^CHINA NATIONAL OFFSHORE OIL|^OFFSHORE OIL ENGINEERING": "CNOOC",
    # CSSC research institutes (702nd = China Ship Scientific Research Center, 710th, 719th...; CSIC merged into CSSC in 2019)
    r"CHINA STATE SHIPBUILDING|CHINA SHIPBUILDING|\bCSSC\b|\bCSIC\b"
    r"|^CHINA SHIP SCIENTIFIC RESEARCH CENT": "CHINA STATE SHIPBUILDING (CSSC)",
    r"^HARBIN ENG|HARBIN ENGINEERING UNIVERSITY": "HARBIN ENGINEERING UNIVERSITY",
    r"SHENYANG INST(ITUTE)? OF AUTOMATION": "SHENYANG INSTITUTE OF AUTOMATION (CAS)",   # also "CHINESE ACADEMY OF SHENYANG INSTITUTE OF AUTOMATION"
    r"^ZHEJIANG UNIV": "ZHEJIANG UNIVERSITY",
    # the same Chinese universities under different English translations, and their local institutes
    r"NORTHWEST(ERN)? (POLYTECHNICAL|INDUSTRY|INDUSTRIAL) UNIVERSITY": "NORTHWESTERN POLYTECHNICAL UNIVERSITY",
    r"^SHANGHAI (JIAO ?TONG|TRAFFIC) UNIVERSITY": "SHANGHAI JIAO TONG UNIVERSITY",
    # China Huaneng Group: Huaneng Power International and its branches, Xi'an Thermal Power Research Institute
    r"^HUANENG|^HUA ENERGY INTERNATIONAL ELECTRIC POWER|INTERNATIONAL ELECTRIC POWER JIANGSU ENERGY"
    r"|^XIAN THERMAL POWER RESEARCH|^XIAN INSTITUTE OF THERMAL RESEARCH": "CHINA HUANENG GROUP",
    r"^CHINA THREE GORGES|^THREE GORGES|^YANGTZE RIVER THREE GORGES": "CHINA THREE GORGES",
    r"^BAE SYSTEMS": "BAE SYSTEMS",
    r"^FUNDACION TECNALIA": "TECNALIA",
    r"^SIEMENS": "SIEMENS",
    r"^KOREA INST(ITUTE)? OF OCEAN": "KIOST",
    r"^MITSUBISHI HEAVY": "MITSUBISHI HEAVY INDUSTRIES",
    r"^KAWASAKI": "KAWASAKI HEAVY INDUSTRIES",
    r"^IHI\b": "IHI",                                         # IHI Corporation (ex Ishikawajima-Harima) and IHI Aerospace
    # duplicates found in the Step 2 screens (Nov 2026), each one checked on the companies' own reports
    r"^SAUDI ARABIAN OIL|^ARAMCO|^沙特阿拉伯石油": "SAUDI ARAMCO",   # Aramco Services / Americas = US subsidiaries; also the Chinese name
    r"^CGG|^SERCEL": "CGG (VIRIDIEN)",                        # CGGVeritas renamed CGG (2013), Viridien (2024); Sercel is its equipment unit
    r"SCHLUMBERGER|^SLB$": "SLB (SCHLUMBERGER)",              # Schlumberger Technology / Canada / Holdings, Services Pétroliers Schlumberger
    r"^ONESUBSEA": "ONESUBSEA",                               # OneSubsea and its IP holding (OneSubsea IP UK)
    r"^MAGSEIS|^FAIRFIELD (IND|NODAL)": "MAGSEIS FAIRFIELD (TGS)",  # Fairfield + Magseis merged in 2018, bought by TGS in 2022
    r"^ISRAEL AEROSPACE|^ELTA SYSTEMS": "ISRAEL AEROSPACE INDUSTRIES (ELTA)",   # ELTA is a subsidiary of IAI
    r"(UNITED STATES|USA|GOVERNMENT OF THE (UNITED STATES|USA)).*(SECRETARY|SEC|DEPARTMENT) OF THE NAVY": "US NAVY",
    r"\bCRRC\b": "CRRC",                                      # CRRC Zhuzhou Institute, Times Electric, SMD (Shanghai), Dalian
    r"STATE GRID|^SGCC": "STATE GRID (SGCC)",                 # State Grid Corporation of China and its provincial power companies
    r"^CGN\b|^CHINA GENERAL NUCLEAR": "CGN (CHINA GENERAL NUCLEAR POWER)",   # CGN Power is the listed arm of China General Nuclear
    r"DEEP ?(IN)?FAR OCEAN": "DEEPINFAR OCEAN TECHNOLOGY",   # v7: Deepinfar (Tianjin) also appears as "Deepfar" in PATSTAT
}

# ----------------------------------------------------------------------------- applicant type from the name (v7)
# Used only when psn_sector is empty or UNKNOWN (pw_utils.applicant_type), after the university and institute rules:
# about 11% of the applicant weight had no type in v6, e.g. CSSC, CNOOC, US Navy and many Chinese "... Technology" firms.
GOV_NAME_PATTERN = (r"\bNAVY\b|\bARMY\b|\bAIR FORCE\b|\bMINIST|\bGOVERNMENT\b|\bADMINISTRATION\b|\bBUREAU\b"
                    r"|\bSURVEY\b|\bAGENCY\b|\bCOAST GUARD\b|\bCOMMISSION\b|FEDERATSIYA|UCHREZHDENIE|\bSTATE OCEANIC"
                    r"|\bSECRETARY OF\b|\bDEPARTMENT OF\b|\bCOMMISSARIAT\b|\bCNRS\b|\bCNES\b|\bNASA\b")
FIRM_NAME_PATTERN = (r"TECHNOLOG|\bTECH\b|\bENGINEERING\b|INDUSTR|\bELECTRIC|\bELECTRONIC|\bEQUIPMENT\b|INTELLIGEN"
                     r"|\bROBOTIC|\bGROUP\b|\bHOLDING|SHIPBUILDING|SHIPYARD|\bOFFSHORE\b|\bENERGY\b|\bPOWER\b|\bSYSTEMS?\b"
                     r"|\bMACHINERY\b|MANUFACTUR|\bTRADING\b|\bDEVELOPMENT\b|\bINSTRUMENT|\bAUTOMATION\b|\bMARINE\b"
                     r"|\bOCEAN\b|\bSUBSEA\b|HANWHA|PETROBRAS|PETROLEO|THREE GORGES|AKTSIONERNOE|OBSHCHESTVO|\bCSSC\b|\bCNOOC\b|\bCRRC\b|\bSGCC\b|\bCGN\b|\bSERVICES?\b|\bSCIENCE AND\b")
# known underwater-vehicle players: if several are missing, the query or the aliases are probably too narrow
KNOWN_PLAYERS = (
    "KONGSBERG", "SAAB", "OCEANEERING", "TELEDYNE", "SUBSEA 7", "FUGRO", "HUNTINGTON INGALLS (HYDROID)",
    "GENERAL DYNAMICS (BLUEFIN)", "EXAIL (ECA)", "NAUTICUS ROBOTICS", "HARBIN ENGINEERING UNIVERSITY",
    "SHENYANG INSTITUTE OF AUTOMATION (CAS)", "TKMS (ATLAS ELEKTRONIK)", "NAVAL GROUP", "L3HARRIS",
    "CHINA STATE SHIPBUILDING (CSSC)",
)

# ----------------------------------------------------------------------------- legal events (tls231)
# Regex on the INPADOC event category title / event text, to group events into the families of the lectures
# (IM_Theory_1 and 2). Check the codes that appear with tls803_legal_event_code before trusting a category.
LEGAL_EVENT_PATTERNS = {
    # matched on "tls803 event_descr | event_category_title | tls231 event_text" (lower case). Negative cases first:
    # "patent expired due to non-payment of maintenance fees" is a LAPSE, not a renewal; "no opposition filed" is NOT
    # an opposition. EP renewals are event code PGFP ("annual fee paid to national office", Tutorial 0, example 5).
    "renewal_fee": r"^(?!.*(?:non-?payment|nonpayment|failure to pay|expired|reminder|not paid|lapse)).*"
                   r"(?:fee paid|fees paid|payment of (?:annual|renewal|maintenance) fee|\| payment\b|ip right maintenance)",
    "lapse": r"lapse|ceased|cessation|expir|non-?payment|nonpayment|termination of patent right",
    "withdrawal_refusal": r"withdraw|refus|reject|deemed to be withdrawn",
    "opposition": r"^(?!.*\bno opposition\b).*\boppos",
    "revocation": r"revoc|nullit|invalidat",
    "transfer": r"transfer|assignment|change of owner|change of proprietor|change of applicant",
    "licence": r"licen[cs]e",
    "litigation": r"litigation|court|infringement",
}
