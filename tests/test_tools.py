import inspect

import pytest

from triage.board import reset_from_seed
from triage.config import ROOT
from triage.schemas import Route
from triage.tools import (
    MAX_LIMIT,
    READ_TOOL_NAMES,
    catalog_names,
    dispatch_read,
    read_tools,
)

SEED = ROOT / "data" / "seed_board.json"


@pytest.fixture
def board(tmp_path):
    return reset_from_seed(SEED, tmp_path / "board.json")


def call(board, name, **args):
    return dispatch_read(name, args, board)


# -- tool surface ---------------------------------------------------------
def test_tool_names_signatures_and_docstrings(board):
    tools = read_tools(board)
    assert tuple(tools) == READ_TOOL_NAMES
    assert list(inspect.signature(tools["search_tickets"]).parameters) == ["query", "limit"]
    assert list(inspect.signature(tools["get_ticket"]).parameters) == ["ref"]
    assert list(inspect.signature(tools["read_catalog"]).parameters) == ["query"]
    for fn in tools.values():
        assert "Call this" in fn.__doc__


# -- search_tickets -------------------------------------------------------
def test_search_tickets_returns_summaries(board):
    out = call(board, "search_tickets", query="fall alert events firmware csv")
    first = out["result"][0]
    assert first["ref"] == 101
    assert set(first) == {"ref", "title", "status", "parent_story", "snippet"}
    assert first["parent_story"] == "Data Requests"


def test_search_tickets_clamps_limit(board):
    for i in range(12):
        board.create_task(Route.DATA, f"Battery export {i}", "battery csv", [])
    assert len(call(board, "search_tickets", query="battery", limit=50)["result"]) == MAX_LIMIT
    assert len(call(board, "search_tickets", query="battery", limit=0)["result"]) == 1


def test_search_tickets_truncates_long_queries(board):
    query = "x" * 199 + " pairing bluetooth"   # keywords sit past 200 chars
    assert call(board, "search_tickets", query=query)["result"] == []
    assert call(board, "search_tickets", query="pairing bluetooth")["result"]


# -- get_ticket -----------------------------------------------------------
def test_get_ticket_returns_full_ticket(board):
    out = call(board, "get_ticket", ref=103)["result"]
    assert out["ref"] == 103 and "battery_telemetry" in out["description"]
    assert set(out) == {"ref", "title", "description", "status", "parent_story", "created", "notes"}


def test_get_ticket_accepts_whole_number_float(board):
    # Gemini can send integers as floats (104.0).
    assert call(board, "get_ticket", ref=104.0)["result"]["ref"] == 104


def test_get_ticket_missing_ref_is_error_object(board):
    assert call(board, "get_ticket", ref=999) == {"error": "No ticket with ref 999."}


# -- read_catalog ---------------------------------------------------------
def test_read_catalog_matches_names_and_fields_case_insensitively(board):
    names = [e["name"] for e in call(board, "read_catalog", query="BATTERY Firmware")["result"]]
    assert "battery_telemetry" in names and "Battery Health Dashboard" in names
    entry = call(board, "read_catalog", query="charge_cycles")["result"][0]
    assert set(entry) == {"name", "type", "owner", "refresh", "description", "key_fields"}


def test_read_catalog_caps_results_and_handles_no_match(board):
    assert len(call(board, "read_catalog", query="device serial model firmware")["result"]) <= 5
    assert call(board, "read_catalog", query="tinnitus questionnaire")["result"] == []


def test_catalog_names():
    names = catalog_names()
    assert "Returns Quality Dashboard" in names and "fall_alert_events" in names


# -- dispatch errors ------------------------------------------------------
def test_unknown_tool_returns_error_object(board):
    out = dispatch_read("close_ticket", {"ref": 104}, board)
    assert "Unknown tool 'close_ticket'" in out["error"]
    assert "result" not in out


@pytest.mark.parametrize("name, args", [
    ("get_ticket", {"ref": "abc"}),            # wrong type
    ("get_ticket", {}),                        # missing required
    ("get_ticket", None),                      # no args at all
    ("search_tickets", {"query": "x", "bogus": 1}),  # unexpected argument
])
def test_bad_arguments_return_error_object(board, name, args):
    out = dispatch_read(name, args, board)
    assert out["error"].startswith(f"Bad arguments for {name}")


@pytest.mark.parametrize("query, expected", [
    ("air conduction bone conduction audiogram", "audiogram_results"),      # REQ-004
    ("battery voltage soc streaming", "battery_logs"),                     # REQ-013
    ("mode usage activation", "device_usage_logs"),                        # REQ-014
    ("consonant brightness default memory", "fitting_parameters"),         # REQ-020
])
def test_read_catalog_finds_entries_for_new_requests(board, query, expected):
    names = [e["name"] for e in call(board, "read_catalog", query=query)["result"]]
    assert names[0] == expected
