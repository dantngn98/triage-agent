"""Data model: IntakeRequest, Route, TriageResult, the route-to-field table, TraceRecord.

The schema stays simple (enums, strings, optionals) because the API enforces
structure, not length or paragraph rules; those live in validator.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class IntakeRequest(BaseModel):
    request_id: str        # "REQ-014"
    submitted_by: str      # synthetic role, e.g. "VP Product Management"
    submitted_at: str      # ISO date
    title: str
    body: str


class Route(str, Enum):
    DATA = "Data Requests"
    DASHBOARD = "Dashboard Requests"
    ANALYTICS = "Analytics Requests"
    CLARIFY = "Needs Clarification"
    DUPLICATE = "Duplicate"


class TriageResult(BaseModel):
    route: Route
    route_rationale: str            # 1–2 sentences
    task_title: str                 # <= 80 chars (validator)
    task_description: str           # one paragraph (validator)
    primary_skill: Literal["SQL", "Python", "BI", "None"]
    requires_multiple_disciplines: bool
    caveats_and_pushback: str       # "None" when nothing to flag
    clarifying_question: str | None
    duplicate_of: int | None        # board ticket ref
    catalog_refs: list[str]         # dashboards / sources the task relies on


# --------------------------------------------------------------------------
# Route-to-field table (PLAN.md, "Which fields a route must fill"), as data.
# --------------------------------------------------------------------------
class FieldRule(str, Enum):
    REQUIRED = "required"   # non-empty string / not None
    EMPTY = "empty"         # must be "" (task title and description)
    NULL = "null"           # must be None


@dataclass(frozen=True)
class RouteFields:
    task: FieldRule                 # applies to task_title and task_description
    clarifying_question: FieldRule
    duplicate_of: FieldRule


TASK_ROUTES: frozenset[Route] = frozenset({Route.DATA, Route.DASHBOARD, Route.ANALYTICS})

_TASK = RouteFields(task=FieldRule.REQUIRED, clarifying_question=FieldRule.NULL,
                    duplicate_of=FieldRule.NULL)

ROUTE_FIELDS: dict[Route, RouteFields] = {
    Route.DATA: _TASK,
    Route.DASHBOARD: _TASK,
    Route.ANALYTICS: _TASK,
    Route.CLARIFY: RouteFields(task=FieldRule.EMPTY, clarifying_question=FieldRule.REQUIRED,
                               duplicate_of=FieldRule.NULL),
    Route.DUPLICATE: RouteFields(task=FieldRule.EMPTY, clarifying_question=FieldRule.NULL,
                                 duplicate_of=FieldRule.REQUIRED),
}

# Maps each rule column to the TriageResult fields it governs.
ROUTE_FIELD_TARGETS: dict[str, tuple[str, ...]] = {
    "task": ("task_title", "task_description"),
    "clarifying_question": ("clarifying_question",),
    "duplicate_of": ("duplicate_of",),
}


# --------------------------------------------------------------------------
# Trace record: one JSON line per run, built by tracing.Trace.
# --------------------------------------------------------------------------
# approved/rejected at the gate; manual_review after two failed validations;
# no_act when run with --no-act; error when the run crashed (trace still written).
Decision = Literal["approved", "rejected", "manual_review", "no_act", "error"]


class ToolCallRecord(BaseModel):
    name: str
    args: dict[str, Any]
    result: Any
    latency_s: float


class CallUsage(BaseModel):
    call: Literal["context", "triage"]
    prompt_tokens: int = 0
    tool_use_prompt_tokens: int = 0
    candidates_tokens: int = 0
    thoughts_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0


class TriageAttempt(BaseModel):
    raw: str | None = None
    parsed: TriageResult | None = None
    errors: list[str] = Field(default_factory=list)


class TraceRecord(BaseModel):
    run_id: str
    request_id: str
    model_id: str
    thinking_level: str
    config_hash: str
    mode: Literal["agent", "baseline"] = "agent"
    repeat: int | None = None              # eval repeat number; None outside the eval
    started_at: str | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    context_summary: str | None = None
    attempts: list[TriageAttempt] = Field(default_factory=list)
    usage: list[CallUsage] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    latency_s: float = 0.0
    decision: Decision | None = None
    action: dict[str, Any] | None = None   # what step 5 did, e.g. {"type": "create_task", "ref": 109}
    error: str | None = None
