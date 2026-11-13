"""Unit tests for the Step 0 rules. They do not need PATSTAT: run `python -m pytest tests` from the repo root."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as C  # noqa: E402
from pw_utils import (apply_blocks, applicant_type, clean_country, legal_event_flags, normalise_name,  # noqa: E402
                      regex_to_like_phrases, to_group, wilson)


def family(title, abstract="", codes=()):
    return pd.DataFrame({"docdb_family_id": [1], "titles": [title], "abstracts": [abstract], "cpc_codes": [list(codes)]})


# ---------------------------------------------------------------- sector query

def test_block1_needs_cpc_and_underwater_term():
    assert apply_blocks(family("Docking station", "the ROV docks", ["B63G2008/004"]))["b1_cpc"].iat[0]
    # indexing code alone (e.g. a wind-turbine part) is not enough
    assert not apply_blocks(family("Wind turbine blade", "improve and provide energy", ["B63G2008/002"]))["b1_cpc"].iat[0]


def test_acronyms_are_whole_words():
    out = apply_blocks(family("Method to improve and provide", "", ["B63G8/00"]))
    assert not out["b4_neighbour"].iat[0]
    assert apply_blocks(family("AUVs swarm", "", ["B63G8/00"]))["b4_neighbour"].iat[0]


def test_block2_specific_keywords_with_hyphens_and_spaces():
    assert apply_blocks(family("An autonomous  underwater vehicle"))["b2_specific"].iat[0]
    assert apply_blocks(family("Multi-AUV coordination"))["b2_specific"].iat[0]
    assert apply_blocks(family("Biomimetic robotic-fish"))["b2_specific"].iat[0]


def test_block3_generic_keyword_title_or_abstract_with_b63_b25j():
    assert apply_blocks(family("Remotely-operated vehicle for pipes"))["b3_generic"].iat[0]
    # in the abstract only: needs a B63 or B25J code
    assert not apply_blocks(family("Subsea valve", "operated by a remotely operated vehicle", ["F16K1/00"]))["b3_generic"].iat[0]
    assert apply_blocks(family("Gripper", "for an underwater robot", ["B25J15/00"]))["b3_generic"].iat[0]


def test_block5_acronym_in_title():
    out = apply_blocks(family("ROV tether management"))
    assert out["b5_acronym_title"].iat[0] and out["in_sector"].iat[0]
    assert not apply_blocks(family("Rover wheel", "used with an ROV"))["b5_acronym_title"].iat[0]   # title only


def test_block6_vehicle_with_task_or_underwater_robot():
    # misses of the PROD validation (v3): now selected
    for title in ["Underwater vehicle obstacle avoidance method and system",
                  "Underwater vehicle three-dimensional path planning method",
                  "Composite autonomous uninhabited underwater vehicle",
                  "Underwater operation robot", "Underwater variant glider"]:
        assert apply_blocks(family(title, "", []))["b6_vehicle_task"].iat[0], title
    # a generic underwater vehicle with no task, and a robot that is not under water, stay out
    assert not apply_blocks(family("Underwater vehicle hull coating", "paint composition", []))["b6_vehicle_task"].iat[0]
    assert not apply_blocks(family("Welding robot", "for ship hulls", []))["b6_vehicle_task"].iat[0]


def test_block6_sql_phrases_reach_the_candidate_pool():
    phrases = regex_to_like_phrases(C.SPECIFIC_KEYWORDS + C.GENERIC_KEYWORDS) + list(C.BLOCK6_SQL_PHRASES)
    assert "underwater vehicle" in phrases and "underwater%robot" in phrases


def test_exclusion_rule_removes_energy_devices():
    turbine = apply_blocks(family("A flowing-water driveable turbine assembly", "deployed by an ROV", ["B63G2008/002"]))
    assert turbine["matched"].iat[0] and turbine["ex_energy_device"].iat[0] and not turbine["in_sector"].iat[0]
    # a vehicle term in the title keeps the family in
    rov = apply_blocks(family("Underwater robot for offshore wind turbine piles", "", ["B63G2008/002"]))
    assert not rov["excluded"].iat[0] and rov["in_sector"].iat[0]
    sub = apply_blocks(family("Oil spill response submarine", "underwater operations", ["B63G2008/002"]))
    assert not sub["excluded"].iat[0]          # crew not stated: kept (labelling rule)


def test_like_phrases_have_no_regex_left():
    phrases = regex_to_like_phrases(C.SPECIFIC_KEYWORDS + C.GENERIC_KEYWORDS)
    assert "remotely-operated-vehicle" in phrases and "autonomous underwater" in phrases
    assert not any(ch in p for p in phrases for ch in "()[]?\\|")


# ---------------------------------------------------------------- statistics

def test_wilson_interval():
    lo, hi = wilson(72, 85)       # 72 of 85 = 85%, interval 76-91%
    assert round(lo, 2) == 0.76 and round(hi, 2) == 0.91
    assert all(pd.isna(x) for x in wilson(0, 0))


# ---------------------------------------------------------------- applicants

@pytest.mark.parametrize("raw, group", [
    ("Kongsberg Maritime AS", "KONGSBERG"),
    ("KONGSBERG MARITIME", "KONGSBERG"),
    ("Saab Seaeye Ltd", "SAAB"),
    ("Harbin Engineering Univ.", "HARBIN ENGINEERING UNIVERSITY"),
    ("Oceaneering International, Inc.", "OCEANEERING"),
    ("ATLAS ELEKTRONIK GMBH", "ATLAS ELEKTRONIK"),
    ("NASA", "NASA"),
])
def test_harmonisation(raw, group):
    assert to_group(normalise_name(raw)) == group


def test_applicant_type_and_country():
    assert applicant_type("UNIVERSITY", "X") == "university"
    assert applicant_type(None, "HARBIN ENGINEERING UNIVERSITY") == "university"
    assert applicant_type("COMPANY", "KONGSBERG") == "firm"
    assert pd.isna(clean_country("  ")) and pd.isna(clean_country(None)) and clean_country("NO ") == "NO"


# ---------------------------------------------------------------- legal events

def test_legal_event_flags():
    f = legal_event_flags(pd.Series(["Payment of annual fee", "Lapse because of non-payment of due fees",
                                     "Opposition filed", "Transfer of rights", None]))
    assert f.loc[0, "renewal_fee"] and f.loc[1, "lapse"] and f.loc[2, "opposition"] and f.loc[3, "transfer"]
    assert not f.loc[4].any()


def test_legal_event_negative_cases():
    f = legal_event_flags(pd.Series([
        "PLBE NO OPPOSITION FILED WITHIN TIME LIMIT | IP RIGHT REVIEW REQUEST | STATUS: NO OPPOSITION FILED WITHIN TIME LIMIT",
        "LAPSE FOR FAILURE TO PAY MAINTENANCE FEES | IP RIGHT CESSATION | PATENT EXPIRED DUE TO NONPAYMENT OF MAINTENANCE FEES",
        "TERMINATION OF PATENT RIGHT DUE TO NON-PAYMENT OF ANNUAL FEE | IP RIGHT CESSATION | ",
        "ANNUAL FEE PAID TO NATIONAL OFFICE | PAYMENT | ",
        "OPPOSITION FILED | IP RIGHT REVIEW REQUEST | ",
    ]))
    assert not f.loc[0, "opposition"]                                  # "no opposition" is not an opposition
    assert f.loc[1, "lapse"] and not f.loc[1, "renewal_fee"]           # an expiry for non-payment is not a renewal
    assert f.loc[2, "lapse"] and not f.loc[2, "renewal_fee"]           # CN CF01
    assert f.loc[3, "renewal_fee"] and not f.loc[3, "lapse"]           # EP PGFP
    assert f.loc[4, "opposition"]


def test_shenyang_alias_with_cas_prefix():
    assert to_group(normalise_name("CHINESE ACADEMY OF SHENYANG INSTITUTE OF AUTOMATION")) == \
        "SHENYANG INSTITUTE OF AUTOMATION (CAS)"


# ---------------------------------------------------------------- BM25

def test_bm25_ranks_the_matching_document_first():
    from pw_utils import BM25, tokenize
    docs = ["an autonomous underwater vehicle with sonar", "a wind turbine blade", "a crewed submarine hull"]
    scores = BM25([tokenize(d) for d in docs]).scores(tokenize("autonomous underwater vehicle"))
    assert scores.argmax() == 0 and scores[1] == 0
    assert tokenize(None) == [] and tokenize("ROV-2, AUV") == ["rov", "2", "auv"]


# ---------------------------------------------------------------- counting rules

def test_fractional_country_weights_follow_tutorial():
    from pw_utils import fractional_weights
    persons = pd.DataFrame({"family": [1, 1, 1, 1, 2], "person": ["a", "b", "c", "d", "e"],
                            "country": ["IT", "IT", "DE", None, "US"]})
    w = fractional_weights(persons, "family", "person", require="country")
    shares = w[w["family"] == 1].groupby("country")["weight"].sum()
    assert shares["IT"] == pytest.approx(2 / 3) and shares["DE"] == pytest.approx(1 / 3)
    assert w.groupby("family")["weight"].sum().tolist() == pytest.approx([1, 1])


def test_fractional_subclasses():
    from pw_utils import fractional_weights
    cpc = pd.DataFrame({"family": [1, 1, 1, 1, 1], "subclass": ["B63G", "B63G", "B63C", "B25J", "G05D"]})
    w = fractional_weights(cpc, "family", "subclass")
    assert len(w) == 4 and (w["weight"] == 0.25).all()


# ---------------------------------------------------------------- matching level and grey zones

def test_application_vs_family_level():
    from pw_utils import flags_by_family
    # family 1: the code is on the CN application, the English words on the US one (no code there)
    apps = pd.DataFrame({"docdb_family_id": [1, 1], "titles": ["水下机器人", "Docking for an ROV"],
                         "abstracts": ["", "an underwater vehicle docks"],
                         "cpc_codes": [["B63G2008/004"], []]})
    app_level = flags_by_family(apply_blocks(apps))
    fam = pd.DataFrame({"docdb_family_id": [1], "titles": ["水下机器人 || Docking for an ROV"],
                        "abstracts": ["an underwater vehicle docks"], "cpc_codes": [["B63G2008/004"]]})
    assert not app_level.loc[1, "b1_cpc"]                 # application level: words and code on different applications
    assert apply_blocks(fam)["b1_cpc"].iat[0]             # family level pools them


def test_specific_keywords():
    assert apply_blocks(family("An unmanned underwater glider"))["b2_specific"].iat[0]
    assert apply_blocks(family("Uncrewed submersible hull"))["b2_specific"].iat[0]
    assert apply_blocks(family("Sub-sea docking", "for an AUV", ["B63G2008/008"]))["b1_cpc"].iat[0]


def test_grey_zone_flags():
    from pw_utils import grey_zone_flags
    df = pd.DataFrame({"titles": ["ROV for offshore wind farm inspection", "Trans-medium aerial underwater vehicle",
                                  "USV that launches an AUV", "Robot for nuclear reactor pool"],
                       "abstracts": ["", "", "", ""], "cpc_codes": [[], [], [], []]})
    g = grey_zone_flags(df)
    assert g["gz_offshore_wind"].iat[0] and g["gz_hybrid_air"].iat[1] and g["gz_hybrid_surface"].iat[2]
    assert g["gz_outside_sea"].iat[3]


def test_legal_prefix_and_applicant_type_order():
    assert normalise_name("KABUSHIKI KAISHA TOSHIBA") == "TOSHIBA"
    assert applicant_type("COMPANY GOV NON-PROFIT", "X") == "firm"
    assert applicant_type("COMPANY UNIVERSITY", "X") == "university"
    assert applicant_type("HOSPITAL", "X") == "hospital"
