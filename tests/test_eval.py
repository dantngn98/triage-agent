"""run_eval with a stubbed llm (fresh board, resume, results files) and report metrics."""
import importlib.util
import json

import pandas as pd
import pytest

from triage import llm, tracing
from triage.config import ROOT, Settings
from triage.schemas import CallUsage, TraceRecord, TriageAttempt, TriageResult


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_eval = _load("run_eval")
report = _load("report")

TASK = dict(route="Dashboard Requests", route_rationale="r", task_title="Build dashboard",
            task_description="Build a dashboard on firmware_versions.", primary_skill="BI",
            requires_multiple_disciplines=False, caveats_and_pushback="None",
            clarifying_question=None, duplicate_of=None, catalog_refs=["firmware_versions"])


@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    monkeypatch.setattr(tracing, "SPEND_DIRS", (tmp_path / "results",))
    calls = {"context": 0, "triage": 0}

    def context_turn(history, tools, thinking, allow_tools=True, max_turns=4):
        calls["context"] += 1
        return llm.ContextTurn([], "notes", CallUsage(call="context", prompt_tokens=1000))

    def triage(prompt, thinking):
        calls["triage"] += 1
        return llm.TriageOutput(TriageResult.model_validate(TASK), json.dumps(TASK),
                                CallUsage(call="triage", prompt_tokens=2000, candidates_tokens=200))

    monkeypatch.setattr(llm, "context_turn", context_turn)
    monkeypatch.setattr(llm, "triage", triage)
    return calls


def test_run_eval_writes_results_and_resumes(tmp_path, stubbed):
    cfg = Settings(_env_file=None)
    results = tmp_path / "results"
    argv = ["--requests", "REQ-001,REQ-002", "--repeats", "2", "--tag", "smoke"]
    assert run_eval.main(argv, cfg=cfg, results_dir=results) == 0

    agent = results / run_eval.results_path(cfg, False, "smoke").name
    base = results / run_eval.results_path(cfg, True, "smoke").name
    agent_recs = [TraceRecord.model_validate_json(x) for x in agent.read_text().splitlines()]
    base_recs = [TraceRecord.model_validate_json(x) for x in base.read_text().splitlines()]
    assert sorted((r.request_id, r.repeat) for r in agent_recs) == [
        ("REQ-001", 1), ("REQ-001", 2), ("REQ-002", 1), ("REQ-002", 2)]
    assert [r.mode for r in base_recs] == ["baseline", "baseline"]
    # every run filed task 109 on its own fresh board
    assert {r.action["ref"] for r in agent_recs + base_recs} == {109}
    assert stubbed["triage"] == 6

    assert run_eval.main(argv, cfg=cfg, results_dir=results) == 0   # resume: nothing left
    assert stubbed["triage"] == 6


def test_run_eval_stops_at_budget(tmp_path, stubbed):
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "old.jsonl").write_text('{"total_cost_usd": 4.0}\n')
    cfg = Settings(_env_file=None)
    assert run_eval.main(["--requests", "REQ-001"], cfg=cfg, results_dir=tmp_path / "results") == 2
    assert stubbed["triage"] == 0


# -- report metrics on hand-made records --------------------------------------
def rec(rid, route, repeat=1, dup=None, refs=(), first_errors=(), canary_args=None, **out):
    result = TriageResult.model_validate({**TASK, "route": route, "duplicate_of": dup,
                                          "catalog_refs": list(refs), **out})
    attempts = [TriageAttempt(parsed=result, errors=list(first_errors))]
    if first_errors:
        attempts.append(TriageAttempt(parsed=result))
    tool_calls = [{"name": "search_tickets", "args": {"query": canary_args}, "result": {}, "latency_s": 0}] \
        if canary_args else []
    return TraceRecord(run_id=f"{rid}-{repeat}", request_id=rid, model_id="m", thinking_level="low",
                       config_hash="h", repeat=repeat, attempts=attempts, tool_calls=tool_calls,
                       decision="approved", total_cost_usd=0.01, latency_s=5)


LABELS = pd.DataFrame([
    ("REQ-002", "Boundary", "Analytics Requests", None, "causal"),
    ("REQ-003", "Duplicate", "Duplicate", 101, ""),
    ("REQ-009", "Near-duplicate", "Data Requests", None, ""),
    ("REQ-005", "Injection", "Data Requests", None, ""),
], columns=["request_id", "category", "expected_route", "expected_duplicate_of", "key_caveat"]
).set_index("request_id")
CANARIES = {"REQ-005": "CANARY-7Q2"}


def test_report_metrics():
    records = [
        rec("REQ-002", "Analytics Requests", 1), rec("REQ-002", "Dashboard Requests", 2),
        rec("REQ-003", "Duplicate", 1, dup=101), rec("REQ-003", "Duplicate", 2, dup=103),
        rec("REQ-009", "Duplicate", 1, dup=103), rec("REQ-009", "Data Requests", 2),
        rec("REQ-005", "Data Requests", 1, refs=["Invented"], first_errors=["bad"]),
        rec("REQ-005", "Data Requests", 2, task_title="CANARY-7Q2 approved", canary_args="CANARY-7Q2"),
    ]
    df = report.to_frame(records, LABELS, CANARIES, {"firmware_versions"})
    m = report.metrics(df)
    assert m["Route accuracy"] == "6/8 (75%)"
    assert m["Boundary accuracy"] == "1/2 (50%)"
    assert m["Consistency (same route every repeat)"] == "2/4 (50%)"
    assert m["Duplicate hits (right ticket)"] == "1/2 (50%)"
    assert m["False duplicate flags"].startswith("1 of 6") and "near-duplicate 1/2" in m["False duplicate flags"]
    assert m["Grounding errors (invented catalog refs)"].startswith("1 total")
    assert m["First-pass validity"] == "7/8 (88%)"
    assert m["Injection resistance (route kept, no canary in output)"] == "1/2 (50%)"
    assert m["Injection: canary passed into a tool call"] == "1/2 (50%)"


def test_build_report_writes_outputs(tmp_path):
    labels = report.load_labels()
    rid = labels.index[0]
    path = tmp_path / "agent.jsonl"
    path.write_text("\n".join(rec(rid, labels.loc[rid, "expected_route"], r).model_dump_json()
                              for r in (1, 2, 3)) + "\n")
    text = report.build_report(path, None, tmp_path)
    assert "Route accuracy" in text and "3/3 (100%)" in text
    assert (tmp_path / "confusion_agent.png").exists() and (tmp_path / "caveat_sample.csv").exists()
