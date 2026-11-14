"""Each validator rule has a passing and a failing case."""
import pytest

from triage.board import reset_from_seed
from triage.config import ROOT
from triage.schemas import TriageResult
from triage.validator import (
    check_caveats_present,
    check_catalog_grounding,
    check_duplicate_exists,
    check_parse,
    check_route_consistency,
    check_single_paragraph,
    check_title_length,
    validate,
)


@pytest.fixture
def board(tmp_path):
    return reset_from_seed(ROOT / "data" / "seed_board.json", tmp_path / "board.json")


TASK = dict(
    route="Dashboard Requests",
    route_rationale="Ongoing monitoring.",
    task_title="Build firmware 3.2 rollout dashboard",
    task_description="Build a daily BI dashboard on firmware_versions showing devices per version and model.",
    primary_skill="BI",
    requires_multiple_disciplines=False,
    caveats_and_pushback="Devices that rarely sync will report late.",
    clarifying_question=None,
    duplicate_of=None,
    catalog_refs=["firmware_versions"],
)
CLARIFY = {**TASK, "route": "Needs Clarification", "task_title": "", "task_description": "",
           "primary_skill": "None", "clarifying_question": "Which metric?", "catalog_refs": []}
DUPLICATE = {**TASK, "route": "Duplicate", "task_title": "", "task_description": "",
             "primary_skill": "None", "duplicate_of": 101}


def r(base=TASK, **overrides) -> TriageResult:
    return TriageResult.model_validate({**base, **overrides})


# -- parse -----------------------------------------------------------------
def test_parse_pass():
    assert check_parse(r()) == []


def test_parse_fail_uses_given_error():
    assert check_parse(None, "bad json") == ["bad json"]
    assert check_parse(None) == ["Output did not parse as TriageResult."]


# -- route consistency -----------------------------------------------------
@pytest.mark.parametrize("base", [TASK, CLARIFY, DUPLICATE], ids=["task", "clarify", "duplicate"])
def test_route_consistency_pass(base):
    assert check_route_consistency(r(base)) == []


@pytest.mark.parametrize("base, overrides, fragment", [
    (TASK, {"task_title": "  "}, "requires task_title to be filled"),
    (TASK, {"clarifying_question": "Why?"}, "requires clarifying_question to be null"),
    (TASK, {"duplicate_of": 101}, "requires duplicate_of to be null"),
    (CLARIFY, {"clarifying_question": None}, "requires clarifying_question to be filled"),
    (CLARIFY, {"task_description": "Do things."}, "requires task_description to be an empty string"),
    (DUPLICATE, {"duplicate_of": None}, "requires duplicate_of to be filled"),
    (DUPLICATE, {"task_title": "Something"}, "requires task_title to be an empty string"),
], ids=["task-no-title", "task-question", "task-dup", "clarify-no-question",
        "clarify-desc", "dup-no-ref", "dup-title"])
def test_route_consistency_fail(base, overrides, fragment):
    errors = check_route_consistency(r(base, **overrides))
    assert any(fragment in e for e in errors), errors


# -- single paragraph ------------------------------------------------------
def test_single_paragraph_pass():
    assert check_single_paragraph(r()) == []
    assert check_single_paragraph(r(CLARIFY)) == []    # empty description is fine
    assert check_single_paragraph(r(task_description="Compare cohorts - before and after.")) == []


@pytest.mark.parametrize("desc, fragment", [
    ("Pull the data.\nThen chart it.", "no line breaks"),
    ("Steps:\n- pull data\n- chart it", "bullet points"),
    ("Steps:\n1. pull data\n2. chart it", "bullet points"),
    ("• pull data", "bullet points"),
    (" ".join(["word"] * 121), "121 words"),
])
def test_single_paragraph_fail(desc, fragment):
    errors = check_single_paragraph(r(task_description=desc))
    assert any(fragment in e for e in errors), errors


# -- title length ------------------------------------------------------------
def test_title_length_pass():
    assert check_title_length(r(task_title="x" * 80)) == []


def test_title_length_fail():
    assert check_title_length(r(task_title="x" * 81)) == [
        "task_title has 81 characters; keep it to 80 or fewer."]


# -- duplicate exists --------------------------------------------------------
def test_duplicate_exists_pass(board):
    assert check_duplicate_exists(r(DUPLICATE), board) == []
    assert check_duplicate_exists(r(), board) == []     # no duplicate claimed


@pytest.mark.parametrize("ref", [999, 2])   # invented ref; a parent story is not a ticket
def test_duplicate_exists_fail(board, ref):
    errors = check_duplicate_exists(r(DUPLICATE, duplicate_of=ref), board)
    assert errors and f"duplicate_of {ref} is not a ticket" in errors[0]


# -- catalog grounding -------------------------------------------------------
def test_catalog_grounding_pass():
    assert check_catalog_grounding(r(catalog_refs=["firmware_versions", "Battery Health Dashboard"])) == []
    assert check_catalog_grounding(r(catalog_refs=[])) == []


def test_catalog_grounding_fail():
    errors = check_catalog_grounding(r(catalog_refs=["firmware_versions", "Firmware Rollout Dashboard",
                                                      "battery health dashboard"]))
    assert len(errors) == 1
    assert "'Firmware Rollout Dashboard'" in errors[0] and "'battery health dashboard'" in errors[0]


# -- caveats present ---------------------------------------------------------
def test_caveats_present_pass():
    assert check_caveats_present(r()) == []
    assert check_caveats_present(r(caveats_and_pushback="None")) == []


def test_caveats_present_fail():
    assert check_caveats_present(r(caveats_and_pushback="   ")) != []


# -- validate() --------------------------------------------------------------
def test_validate_clean_result_passes(board):
    assert validate(r(), board) == []


def test_validate_collects_errors_from_every_rule(board):
    bad = r(task_title="x" * 90, task_description="a\nb", duplicate_of=999,
            catalog_refs=["Made Up Source"], caveats_and_pushback="")
    errors = validate(bad, board)
    for fragment in ("duplicate_of to be null", "no line breaks", "90 characters",
                     "duplicate_of 999", "Made Up Source", "caveats_and_pushback is empty"):
        assert any(fragment in e for e in errors), fragment


def test_validate_parse_failure_short_circuits(board):
    assert validate(None, board, parse_error="truncated JSON") == ["truncated JSON"]


def test_few_shot_examples_pass_validator(board):
    from triage.render import load_examples
    for ex in load_examples():
        assert validate(TriageResult.model_validate(ex["output"]), board) == [], ex["output"]["route"]
