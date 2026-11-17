"""Context loop -> triage -> validate -> approve -> act.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from triage import approval, llm
from triage.board import Board
from triage.config import Settings
from triage.render import render_request, render_triage
from triage.schemas import (
    TASK_ROUTES,
    CallUsage,
    IntakeRequest,
    Route,
    ToolCallRecord,
    TraceRecord,
    TriageResult,
)
from triage.tools import dispatch_read, read_tools
from triage.tracing import Trace, check_budget, write_trace
from triage.validator import validate

MAX_TRIAGE_ATTEMPTS = 2   # first try plus one retry with the validator errors


@dataclass
class ContextOutcome:
    notes: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    usage: list[CallUsage] = field(default_factory=list)


# --------------------------------------------------------------------------
# Steps 2 and 3
# --------------------------------------------------------------------------
def gather_context(req: IntakeRequest, board: Board, cfg: Settings) -> ContextOutcome:
    """Step 2: the model chooses read tools for up to max_context_turns turns.

    Tools are switched off on the final turn so the loop always ends with a
    text summary, even when the model would have kept searching.
    """
    tools = list(read_tools(board).values())
    history = llm.new_history(render_request(req))
    out = ContextOutcome(notes="")
    for turn in range(cfg.max_context_turns):
        last = turn == cfg.max_context_turns - 1
        reply = llm.context_turn(history, tools, cfg.thinking_level,
                                 allow_tools=not last, max_turns=cfg.max_context_turns)
        out.usage.append(reply.usage)
        if not reply.calls:
            out.notes = reply.text
            break
        results = []
        for call in reply.calls:
            t0 = time.perf_counter()
            response = dispatch_read(call.name, call.args, board)   # read tools only
            out.tool_calls.append(ToolCallRecord(
                name=call.name, args=call.args, result=response,
                latency_s=round(time.perf_counter() - t0, 4)))
            results.append((call, response))
        llm.add_tool_results(history, results)
    return out


def triage_once(req: IntakeRequest, notes: str, errors: list[str] | None,
                cfg: Settings) -> llm.TriageOutput:
    """Step 3: one structured triage call."""
    return llm.triage(render_triage(req, notes, errors), cfg.thinking_level)


# --------------------------------------------------------------------------
# Step 5: actions, executed by Python only after approval
# --------------------------------------------------------------------------
def act(req: IntakeRequest, result: TriageResult, board: Board, cfg: Settings) -> dict[str, Any]:
    if result.route in TASK_ROUTES:
        description = result.task_description
        if result.caveats_and_pushback.strip() and result.caveats_and_pushback.strip() != "None":
            description += f"\n\nCaveats: {result.caveats_and_pushback.strip()}"
        tags = [result.primary_skill] if result.primary_skill != "None" else []
        if result.requires_multiple_disciplines:
            tags.append("multi-discipline")
        ref = board.create_task(result.route, result.task_title, description, tags,
                                request_id=req.request_id)
        return {"type": "create_task", "ref": ref, "parent": result.route.value}

    if result.route is Route.CLARIFY:
        path = Path(cfg.drafts_dir) / f"{req.request_id}_clarify.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Clarification needed: {req.request_id}\n\n"
            f"**Request:** {req.title}  \n"
            f"**Submitted by:** {req.submitted_by} on {req.submitted_at}\n\n"
            f"## Question\n\n{result.clarifying_question}\n\n"
            f"## Why\n\n{result.route_rationale}\n",
            encoding="utf-8")
        return {"type": "write_clarification", "path": str(path)}

    note = (f"Related request {req.request_id}: \"{req.title}\" from {req.submitted_by}, "
            f"submitted {req.submitted_at}; triaged as duplicate on {date.today().isoformat()}.")
    board.append_note(result.duplicate_of, note)
    return {"type": "append_note", "ref": result.duplicate_of}


# --------------------------------------------------------------------------
# One full run
# --------------------------------------------------------------------------
def run(req: IntakeRequest, board: Board, cfg: Settings, *, baseline: bool = False,
        no_act: bool = False, auto_approve: bool | None = None,
        trace_path: Path | None = None, input_fn: Callable[[str], str] = input,
        out: Callable[[str], None] = print, repeat: int | None = None) -> TraceRecord:
    """Run one request end to end and append its trace. Raises BudgetExceeded before
    any model call once recorded spend reaches the cap."""
    check_budget(cfg)
    auto = cfg.auto_approve if auto_approve is None else auto_approve
    trace = Trace.start(req, cfg, baseline=baseline, repeat=repeat)
    try:
        notes = ""
        if not baseline:
            ctx = gather_context(req, board, cfg)
            for u in ctx.usage:
                trace.add_usage(u)
            trace.add_tool_calls(ctx.tool_calls)
            trace.set_context(ctx.notes)
            notes = ctx.notes

        result, errors = None, []
        for _ in range(MAX_TRIAGE_ATTEMPTS):
            output = triage_once(req, notes, errors or None, cfg)
            trace.add_usage(output.usage)
            result = output.parsed
            errors = validate(result, board, parse_error=output.parse_error)
            trace.record_attempt(result, output.raw, errors)
            if not errors:
                break

        if errors:
            out(approval.summary(req, trace.record, result))
            out("Validation failed twice: marked for manual review; nothing written.")
            decision = "manual_review"
        elif no_act:
            out(approval.summary(req, trace.record, result))
            out("--no-act: nothing written.")
            decision = "no_act"
        elif approval.review(req, trace.record, result, auto=auto, input_fn=input_fn, out=out):
            trace.record.action = act(req, result, board, cfg)
            decision = "approved"
        else:
            decision = "rejected"
    except Exception as exc:
        trace.record.error = f"{type(exc).__name__}: {exc}"
        write_trace(trace.finish("error"), trace_path)
        raise
    record = trace.finish(decision)
    write_trace(record, trace_path)
    return record
