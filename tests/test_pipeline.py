"""End-to-end pipeline with a stubbed llm module: no network, temp board, temp traces."""
import json

import pytest

from triage import __main__ as cli
from triage import llm, pipeline, tracing
from triage.board import JsonBoard, reset_from_seed
from triage.config import ROOT, Settings
from triage.schemas import CallUsage, IntakeRequest, TraceRecord, TriageResult
from triage.tracing import BudgetExceeded

REQ = IntakeRequest(request_id="REQ-001", submitted_by="Firmware PM", submitted_at="2026-11-02",
                    title="Firmware 3.2 rollout dashboard", body="Weekly devices per firmware version.")

TASK = dict(
    route="Dashboard Requests", route_rationale="Ongoing monitoring.",
    task_title="Build firmware 3.2 rollout dashboard",
    task_description="Build a daily BI dashboard on firmware_versions of devices per version and model.",
    primary_skill="BI", requires_multiple_disciplines=True,
    caveats_and_pushback="Devices that rarely sync report late.",
    clarifying_question=None, duplicate_of=None, catalog_refs=["firmware_versions"])
CLARIFY = {**TASK, "route": "Needs Clarification", "task_title": "", "task_description": "",
           "primary_skill": "None", "clarifying_question": "Which metric?", "catalog_refs": []}
DUPLICATE = {**TASK, "route": "Duplicate", "task_title": "", "task_description": "",
             "primary_skill": "None", "duplicate_of": 101}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(tracing, "SPEND_DIRS", (tmp_path / "spend",))
    monkeypatch.setattr(tracing, "TRACES_DIR", tmp_path / "traces")
    cfg = Settings(_env_file=None, board_path=tmp_path / "board.json",
                   drafts_dir=tmp_path / "drafts")
    board = reset_from_seed(ROOT / "data" / "seed_board.json", cfg.board_path)
    return cfg, board, tmp_path / "trace.jsonl"


@pytest.fixture
def stub(monkeypatch):
    """Scripted llm: one search turn then a summary; triage outputs popped from a list."""
    state = {"context_calls": 0, "triage_prompts": [], "outputs": []}

    def context_turn(history, tools, thinking, allow_tools=True, max_turns=4):
        state["context_calls"] += 1
        usage = CallUsage(call="context", prompt_tokens=1000, candidates_tokens=50)
        if state["context_calls"] == 1:
            return llm.ContextTurn([llm.ToolCall("c1", "search_tickets", {"query": "firmware"})], "", usage)
        return llm.ContextTurn([], "No duplicate. Catalog: firmware_versions.", usage)

    def triage(prompt, thinking):
        state["triage_prompts"].append(prompt)
        raw = state["outputs"].pop(0)
        usage = CallUsage(call="triage", prompt_tokens=3000, candidates_tokens=300)
        return llm.TriageOutput(parsed=TriageResult.model_validate(raw), raw=json.dumps(raw), usage=usage)

    monkeypatch.setattr(llm, "context_turn", context_turn)
    monkeypatch.setattr(llm, "add_tool_results", lambda history, results: None)
    monkeypatch.setattr(llm, "triage", triage)
    return state


def run(env, **kw):
    cfg, board, trace_path = env
    kw.setdefault("out", lambda s: None)
    return pipeline.run(REQ, board, cfg, trace_path=trace_path, **kw)


def written(env) -> list[TraceRecord]:
    return [TraceRecord.model_validate_json(line)
            for line in env[2].read_text(encoding="utf-8").splitlines()]


def test_task_route_end_to_end_creates_task_and_trace(env, stub):
    stub["outputs"] = [TASK]
    record = run(env, auto_approve=True)

    assert record.decision == "approved"
    assert record.action == {"type": "create_task", "ref": 109, "parent": "Dashboard Requests"}
    task = JsonBoard(env[0].board_path).get(109)
    assert task.request_id == "REQ-001" and task.tags == ["BI", "multi-discipline"]
    assert task.description.endswith("\n\nCaveats: Devices that rarely sync report late.")

    assert [tc.name for tc in record.tool_calls] == ["search_tickets"]
    assert record.context_summary.startswith("No duplicate")
    assert [u.call for u in record.usage] == ["context", "context", "triage"]
    # (1000+1000+3000) in x 0.75/M + (50+50+300) out x 3.75/M
    assert record.total_cost_usd == pytest.approx(0.00525)
    assert written(env) == [record]
    assert "Context notes: No duplicate." in stub["triage_prompts"][0]


def test_retry_with_validator_errors_then_pass(env, stub):
    stub["outputs"] = [{**TASK, "task_title": "x" * 95}, TASK]
    record = run(env, auto_approve=True)
    assert record.decision == "approved" and len(record.attempts) == 2
    assert "95 characters" in record.attempts[0].errors[0] and record.attempts[1].errors == []
    assert "95 characters" in stub["triage_prompts"][1]


def test_two_failures_mean_manual_review_and_no_write(env, stub):
    stub["outputs"] = [{**TASK, "catalog_refs": ["Invented Source"]}] * 2
    record = run(env, auto_approve=True)
    assert record.decision == "manual_review" and record.action is None
    assert len(JsonBoard(env[0].board_path).search("rollout")) == 0


def test_rejection_writes_nothing(env, stub):
    stub["outputs"] = [TASK]
    answers = iter(["maybe", "n"])
    record = run(env, input_fn=lambda prompt: next(answers))
    assert record.decision == "rejected" and record.action is None
    with pytest.raises(KeyError):
        JsonBoard(env[0].board_path).get(109)


def test_interactive_approval(env, stub):
    stub["outputs"] = [TASK]
    record = run(env, input_fn=lambda prompt: "y")
    assert record.decision == "approved" and record.action["ref"] == 109


def test_clarify_writes_draft_only(env, stub):
    stub["outputs"] = [CLARIFY]
    record = run(env, auto_approve=True)
    draft = env[0].drafts_dir / "REQ-001_clarify.md"
    assert record.action == {"type": "write_clarification", "path": str(draft)}
    assert "Which metric?" in draft.read_text(encoding="utf-8")


def test_duplicate_appends_note(env, stub):
    stub["outputs"] = [DUPLICATE]
    record = run(env, auto_approve=True)
    assert record.action == {"type": "append_note", "ref": 101}
    assert JsonBoard(env[0].board_path).get(101).notes[0].startswith("Related request REQ-001")


def test_baseline_skips_context_loop(env, stub):
    stub["outputs"] = [TASK]
    record = run(env, baseline=True, no_act=True)
    assert stub["context_calls"] == 0 and record.mode == "baseline" and record.tool_calls == []
    assert record.decision == "no_act"
    assert "No context was gathered" in stub["triage_prompts"][0]


def test_budget_exceeded_stops_before_any_model_call(env, stub, tmp_path):
    (tmp_path / "spend").mkdir()
    (tmp_path / "spend" / "old.jsonl").write_text('{"total_cost_usd": 4.0}\n', encoding="utf-8")
    with pytest.raises(BudgetExceeded):
        run(env, auto_approve=True)
    assert stub["context_calls"] == 0 and not env[2].exists()


def test_crash_still_writes_trace_with_error(env, stub, monkeypatch):
    def boom(prompt, thinking):
        raise RuntimeError("API down")
    monkeypatch.setattr(llm, "triage", boom)
    with pytest.raises(RuntimeError):
        run(env, auto_approve=True)
    [record] = written(env)
    assert record.decision == "error" and "API down" in record.error
    assert record.usage and record.total_cost_usd > 0      # context spend still counted


def test_cli_batch_with_auto_approve(env, stub, tmp_path, monkeypatch, capsys):
    cfg = env[0]
    monkeypatch.setattr(cli, "get_settings", lambda: cfg)
    intake = tmp_path / "intake"
    intake.mkdir()
    for rid in ("REQ-001", "REQ-002"):
        (intake / f"{rid}.json").write_text(REQ.model_copy(update={"request_id": rid}).model_dump_json(),
                                           encoding="utf-8")
    stub["outputs"] = [TASK, DUPLICATE]
    assert cli.main(["batch", str(intake), "--auto-approve"]) == 0
    out = capsys.readouterr().out
    assert "REQ-001: decision=approved" in out and "REQ-002: decision=approved" in out
    traces = list((tmp_path / "traces").glob("*.jsonl"))
    assert len(traces) == 1 and len(traces[0].read_text(encoding="utf-8").splitlines()) == 2
