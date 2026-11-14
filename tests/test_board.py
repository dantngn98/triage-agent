import json

import pytest

from triage.board import JsonBoard, TicketNotFound, reset_from_seed, tokenize
from triage.config import ROOT
from triage.schemas import Route

SEED = ROOT / "data" / "seed_board.json"


@pytest.fixture
def board(tmp_path):
    return reset_from_seed(SEED, tmp_path / "board.json")


def test_tokenize_drops_stopwords_and_keeps_versions():
    assert tokenize("Can we get the battery drain on firmware 3.1?") == {
        "battery", "drain", "firmware", "3.1"}


@pytest.mark.parametrize("query, expected_ref", [
    ("fall alert events per firmware version every week as a csv", 101),
    ("bluetooth pairing failures in the app by android os version", 102),
    ("is firmware 3.1 draining RIC batteries faster", 103),
])
def test_search_ranks_paraphrased_seed_ticket_first(board, query, expected_ref):
    results = board.search(query)
    assert results and results[0].ref == expected_ref


def test_search_respects_limit_and_returns_summaries(board):
    results = board.search("dashboard csv firmware app returns", limit=2)
    assert len(results) == 2
    assert all(len(r.snippet) <= 163 for r in results)


def test_search_with_only_stopwords_or_no_match_is_empty(board):
    assert board.search("can we please get this") == []
    assert board.search("xylophone") == []


def test_get_returns_full_ticket_and_raises_for_missing(board):
    t = board.get(103)
    assert t.parent_story is Route.ANALYTICS and "battery_telemetry" in t.description
    with pytest.raises(TicketNotFound):
        board.get(999)


def test_created_tasks_persist_across_reloads(board):
    ref = board.create_task(Route.DASHBOARD, "Firmware 3.2 rollout dashboard",
                            "Build a dashboard.", ["BI"], request_id="REQ-001")
    assert ref == 109
    reloaded = JsonBoard(board.path)
    t = reloaded.get(109)
    assert (t.title, t.parent_story, t.tags, t.request_id, t.status) == (
        "Firmware 3.2 rollout dashboard", Route.DASHBOARD, ["BI"], "REQ-001", "Backlog")
    assert reloaded.create_task(Route.DATA, "x", "y", []) == 110


def test_create_task_rejects_non_task_routes(board):
    for route in (Route.CLARIFY, Route.DUPLICATE):
        with pytest.raises(ValueError):
            board.create_task(route, "t", "d", [])


def test_append_note_persists_and_raises_for_missing(board):
    board.append_note(101, "Related request REQ-003")
    assert JsonBoard(board.path).get(101).notes == ["Related request REQ-003"]
    with pytest.raises(TicketNotFound):
        board.append_note(999, "x")


def test_reset_discards_writes_and_never_touches_seed(board):
    seed_before = SEED.read_text(encoding="utf-8")
    board.create_task(Route.DATA, "t", "d", [])
    fresh = reset_from_seed(SEED, board.path)
    assert len(json.loads(fresh.path.read_text(encoding="utf-8"))["tickets"]) == 8
    assert SEED.read_text(encoding="utf-8") == seed_before


def test_missing_board_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        JsonBoard(tmp_path / "nope.json")
