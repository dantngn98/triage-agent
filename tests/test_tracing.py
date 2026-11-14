import json

import pytest

from triage.config import Settings
from triage.schemas import CallUsage, IntakeRequest, ToolCallRecord, TraceRecord, TriageResult
from triage.tracing import (
    BudgetExceeded,
    Trace,
    call_cost,
    check_budget,
    config_hash,
    total_spend,
    write_trace,
)

REQ = IntakeRequest(request_id="REQ-001", submitted_by="PM", submitted_at="2026-11-01",
                    title="t", body="b")
CFG = Settings(_env_file=None)


def test_cost_from_fake_usage_bills_thinking_as_output():
    u = CallUsage(call="triage", prompt_tokens=8_000, tool_use_prompt_tokens=1_000,
                  candidates_tokens=1_500, thoughts_tokens=500)
    # 9,000 in x $0.75/M + 2,000 out x $3.75/M = 0.00675 + 0.0075
    assert call_cost(u) == pytest.approx(0.01425)
    assert call_cost(CallUsage(call="context")) == 0


def test_trace_builds_record_through_a_run():
    trace = Trace.start(REQ, CFG, run_id="run-1")
    trace.add_usage(CallUsage(call="context", prompt_tokens=1_000_000))             # $0.75
    trace.add_usage(CallUsage(call="triage", candidates_tokens=100_000,
                              thoughts_tokens=100_000))                          # $0.75
    trace.add_tool_calls([ToolCallRecord(name="get_ticket", args={"ref": 101},
                                         result={"result": {}}, latency_s=0.01)])
    trace.set_context("notes")
    trace.record_attempt(None, "{bad", ["did not parse"])
    record = trace.finish("manual_review")

    assert (record.run_id, record.request_id, record.mode, record.decision) == (
        "run-1", "REQ-001", "agent", "manual_review")
    assert [u.cost_usd for u in record.usage] == [pytest.approx(0.75), pytest.approx(0.75)]
    assert record.total_cost_usd == pytest.approx(1.50)
    assert record.attempts[0].errors == ["did not parse"] and record.context_summary == "notes"
    assert record.latency_s >= 0 and record.config_hash == config_hash(CFG)


def test_config_hash_tracks_settings_and_mode():
    assert config_hash(CFG) == config_hash(Settings(_env_file=None))
    assert config_hash(CFG) != config_hash(Settings(_env_file=None, thinking_level="medium"))
    assert config_hash(CFG) != config_hash(CFG, baseline=True)
    assert Trace.start(REQ, CFG, baseline=True).record.mode == "baseline"


def test_write_trace_appends_one_line_per_run(tmp_path):
    path = tmp_path / "t.jsonl"
    for i in range(2):
        t = Trace.start(REQ, CFG, run_id=f"r{i}")
        t.record_attempt(TriageResult.model_validate({
            "route": "Data Requests", "route_rationale": "x", "task_title": "x",
            "task_description": "x", "primary_skill": "SQL",
            "requires_multiple_disciplines": False, "caveats_and_pushback": "None",
            "clarifying_question": None, "duplicate_of": None, "catalog_refs": []}), "{}", [])
        write_trace(t.finish("approved"), path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [TraceRecord.model_validate_json(line).run_id for line in lines] == ["r0", "r1"]


def _spend_file(path, *costs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"total_cost_usd": c}) + "\n" for c in costs), encoding="utf-8")


def test_total_spend_sums_traces_and_results_and_skips_bad_lines(tmp_path):
    traces, results = tmp_path / "traces", tmp_path / "results"
    _spend_file(traces / "2026-11-14.jsonl", 0.5, 0.25)
    _spend_file(results / "agent.jsonl", 1.0)
    with (results / "agent.jsonl").open("a", encoding="utf-8") as f:
        f.write("not json\n")
    assert total_spend([traces, results, tmp_path / "missing"]) == pytest.approx(1.75)


def test_budget_check_blocks_at_the_cap(tmp_path):
    _spend_file(tmp_path / "t.jsonl", 3.99)
    assert check_budget(CFG, [tmp_path]) == pytest.approx(3.99)
    _spend_file(tmp_path / "u.jsonl", 0.01)          # total now exactly 4.00
    with pytest.raises(BudgetExceeded, match="cap"):
        check_budget(CFG, [tmp_path])
