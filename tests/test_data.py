"""Synthetic data files parse and are internally consistent. Labels are not checked here."""
import json
from pathlib import Path

import pytest
import yaml

from triage.config import ROOT
from triage.schemas import TASK_ROUTES, IntakeRequest

DATA = ROOT / "data"
INTAKE_FILES = sorted((DATA / "intake").glob("*.json"))


def test_twenty_intake_files():
    assert len(INTAKE_FILES) == 20


@pytest.mark.parametrize("path", INTAKE_FILES, ids=lambda p: p.stem)
def test_intake_file_parses(path: Path):
    req = IntakeRequest.model_validate_json(path.read_text(encoding="utf-8"))
    assert req.request_id == path.stem
    assert req.title.strip() and req.body.strip()


def test_catalog_entries_well_formed():
    entries = yaml.safe_load((DATA / "catalog.yaml").read_text(encoding="utf-8"))["entries"]
    assert 10 <= len(entries) <= 15
    names = [e["name"] for e in entries]
    assert len(names) == len(set(names))
    for e in entries:
        assert e["type"] in {"dashboard", "source"}
        assert e["owner"] and e["refresh"] and e["key_fields"]


def test_seed_board_structure():
    board = json.loads((DATA / "seed_board.json").read_text(encoding="utf-8"))
    assert {s["route"] for s in board["stories"]} == {r.value for r in TASK_ROUTES}
    tickets = board["tickets"]
    assert len(tickets) == 8
    refs = [t["ref"] for t in tickets] + [s["ref"] for s in board["stories"]]
    assert len(refs) == len(set(refs))
    for t in tickets:
        assert t["parent_story"] in {r.value for r in TASK_ROUTES}
        assert {"title", "description", "status", "created"} <= set(t)


def test_each_canary_appears_only_in_its_request():
    canaries = json.loads((DATA / "canaries.json").read_text(encoding="utf-8"))
    assert len(canaries) == 3 and len(set(canaries.values())) == 3
    others = [DATA / "catalog.yaml", DATA / "seed_board.json"]
    for request_id, canary in canaries.items():
        for path in INTAKE_FILES + others:
            text = path.read_text(encoding="utf-8")
            assert (canary in text) == (path.stem == request_id), (canary, path.name)
