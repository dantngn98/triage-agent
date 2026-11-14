import pytest
from pydantic import ValidationError

from triage.schemas import (
    ROUTE_FIELD_TARGETS,
    ROUTE_FIELDS,
    TASK_ROUTES,
    FieldRule,
    IntakeRequest,
    Route,
    TraceRecord,
    TriageResult,
)


def make_result(**overrides) -> dict:
    base = dict(
        route="Dashboard Requests",
        route_rationale="Asks for ongoing monitoring of a metric.",
        task_title="Build fall-alert events dashboard",
        task_description="Build a BI dashboard of daily fall-alert events by firmware version.",
        primary_skill="BI",
        requires_multiple_disciplines=False,
        caveats_and_pushback="None",
        clarifying_question=None,
        duplicate_of=None,
        catalog_refs=["Fall Alert Events"],
    )
    base.update(overrides)
    return base


def test_triage_result_json_schema_generates():
    schema = TriageResult.model_json_schema()
    assert set(schema["required"]) == set(TriageResult.model_fields)
    route_def = schema["$defs"]["Route"]
    assert route_def["enum"] == [r.value for r in Route]


def test_triage_result_parses_and_round_trips():
    result = TriageResult.model_validate(make_result())
    assert result.route is Route.DASHBOARD
    assert TriageResult.model_validate_json(result.model_dump_json()) == result


def test_triage_result_rejects_unknown_route_and_skill():
    with pytest.raises(ValidationError):
        TriageResult.model_validate(make_result(route="Ops Requests"))
    with pytest.raises(ValidationError):
        TriageResult.model_validate(make_result(primary_skill="Excel"))


def test_intake_request_parses():
    req = IntakeRequest(request_id="REQ-014", submitted_by="VP Product Management",
                        submitted_at="2026-11-02", title="t", body="b")
    assert req.request_id == "REQ-014"


def test_route_fields_cover_every_route():
    assert set(ROUTE_FIELDS) == set(Route)
    assert TASK_ROUTES == {Route.DATA, Route.DASHBOARD, Route.ANALYTICS}


@pytest.mark.parametrize(
    "route, task, question, dup",
    [
        (Route.DATA, FieldRule.REQUIRED, FieldRule.NULL, FieldRule.NULL),
        (Route.DASHBOARD, FieldRule.REQUIRED, FieldRule.NULL, FieldRule.NULL),
        (Route.ANALYTICS, FieldRule.REQUIRED, FieldRule.NULL, FieldRule.NULL),
        (Route.CLARIFY, FieldRule.EMPTY, FieldRule.REQUIRED, FieldRule.NULL),
        (Route.DUPLICATE, FieldRule.EMPTY, FieldRule.NULL, FieldRule.REQUIRED),
    ],
)
def test_route_fields_match_plan_table(route, task, question, dup):
    rule = ROUTE_FIELDS[route]
    assert (rule.task, rule.clarifying_question, rule.duplicate_of) == (task, question, dup)


def test_route_field_targets_name_real_fields():
    targets = {f for fields in ROUTE_FIELD_TARGETS.values() for f in fields}
    assert targets <= set(TriageResult.model_fields)
    rule_columns = set(ROUTE_FIELDS[Route.DATA].__dataclass_fields__)
    assert set(ROUTE_FIELD_TARGETS) == rule_columns


def test_trace_record_defaults_serialize():
    trace = TraceRecord(run_id="r1", request_id="REQ-001", model_id="m",
                        thinking_level="low", config_hash="abc")
    assert TraceRecord.model_validate_json(trace.model_dump_json()) == trace
    assert trace.decision is None and trace.tool_calls == []
