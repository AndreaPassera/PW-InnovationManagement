import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

with open(ROOT / "config" / "sector.yaml", encoding="utf-8") as f:
    _cfg = yaml.safe_load(f)

for _key, _value in _cfg.items():
    globals()[_key.upper()] = tuple(_value) if isinstance(_value, list) else _value

ENV = os.environ.get("PW_ENV", ENV)
IPR_TYPE = os.environ.get("PW_IPR_TYPE", IPR_TYPE)

LAST_COMPLETE_YEAR = YEAR_MAX - TRUNCATION_WINDOW

LABEL_DIR = Path(LABEL_DIR)
SECOND_LABEL_DIR = LABEL_DIR / "second"

_suffix = "" if IPR_TYPE == "PI" else f"_{IPR_TYPE}"
OUT_STEP0 = Path(OUTPUT_DIR) / ENV.lower() / f"step0{_suffix}"
OUT_STEP1 = Path(OUTPUT_DIR) / ENV.lower() / f"step1{_suffix}"
