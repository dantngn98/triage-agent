"""gather_context with a stubbed llm: tool dispatch, turn cap, last-turn tools off."""
import pytest

from triage import llm, pipeline
from triage.board import reset_from_seed
from triage.config import ROOT, Settings
from triage.schemas import CallUsage, IntakeRequest

REQ = IntakeRequest(request_id="REQ-900", submitted_by="PM", submitted_at="2026-11-01",
                    title="Fall alerts weekly", body="Weekly CSV of fall alert events by firmware.")


@pytest.fixture
def board(tmp_path):
    return reset_from_seed(ROOT / "data" / "seed_board.json", tmp_path / "board.json")


def stub_turns(monkeypatch, script):
    """script: list of call lists; an empty list means the model answers in text."""
    seen = []

    def fake_context_turn(history, tools, thinking, allow_tools=True, max_turns=4):
        seen.append(allow_tools)
        calls = script[len(seen) - 1] if allow_tools else []
        text = "" if calls else f"summary after {len(seen)} turn(s)"
        return llm.ContextTurn(calls=calls, text=text, usage=CallUsage(call="context"))

    monkeypatch.setattr(llm, "context_turn", fake_context_turn)
    monkeypatch.setattr(llm, "add_tool_results", lambda history, results: None)
    return seen


def test_dispatches_calls_then_stops_on_text(monkeypatch, board):
    seen = stub_turns(monkeypatch, [
        [llm.ToolCall("1", "search_tickets", {"query": "fall alert firmware"}),
         llm.ToolCall("2", "read_catalog", {"query": "fall alert"})],
        [llm.ToolCall("3", "get_ticket", {"ref": 101})],
        [],
    ])
    out = pipeline.gather_context(REQ, board, Settings(_env_file=None))
    assert seen == [True, True, True]
    assert [tc.name for tc in out.tool_calls] == ["search_tickets", "read_catalog", "get_ticket"]
    assert out.tool_calls[2].result["result"]["ref"] == 101
    assert out.notes == "summary after 3 turn(s)" and len(out.usage) == 3


def test_turn_cap_switches_tools_off_on_last_turn(monkeypatch, board):
    always_search = [[llm.ToolCall(str(i), "search_tickets", {"query": "battery"})] for i in range(10)]
    seen = stub_turns(monkeypatch, always_search)
    out = pipeline.gather_context(REQ, board, Settings(_env_file=None, max_context_turns=4))
    assert seen == [True, True, True, False]
    assert len(out.tool_calls) == 3 and out.notes == "summary after 4 turn(s)"


def test_unknown_tool_from_model_becomes_error_result(monkeypatch, board):
    stub_turns(monkeypatch, [[llm.ToolCall("1", "close_ticket", {"ref": 104})], []])
    out = pipeline.gather_context(REQ, board, Settings(_env_file=None))
    assert "Unknown tool" in out.tool_calls[0].result["error"]
    assert board.get(104).status == "Done"   # untouched
