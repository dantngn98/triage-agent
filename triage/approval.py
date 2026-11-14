"""Terminal approval gate: show everything the agent saw and decided, then read y/n."""
from __future__ import annotations

import json
import textwrap
from typing import Callable

from triage.schemas import TASK_ROUTES, IntakeRequest, Route, TraceRecord, TriageResult

WIDTH = 100


def _wrap(text: str, indent: str = "   ") -> str:
    return "\n".join(textwrap.fill(p, WIDTH, initial_indent=indent, subsequent_indent=indent)
                     if p.strip() else "" for p in text.splitlines()) or indent + "(empty)"


def planned_action(result: TriageResult, req: IntakeRequest) -> str:
    if result.route in TASK_ROUTES:
        return f"Create a task under '{result.route.value}'"
    if result.route is Route.CLARIFY:
        return f"Write drafts/{req.request_id}_clarify.md with the question; no ticket"
    return f"Append 'Related request {req.request_id}' to ticket {result.duplicate_of}"


def summary(req: IntakeRequest, record: TraceRecord, result: TriageResult | None) -> str:
    lines = [
        "=" * WIDTH,
        f"{req.request_id}  {req.title}",
        f"Submitted by {req.submitted_by} on {req.submitted_at}",
        "-" * WIDTH,
        "REQUEST BODY (untrusted)",
        _wrap(req.body),
        "",
        f"TOOL CALLS ({len(record.tool_calls)})" + ("  [baseline: context loop skipped]"
                                                    if record.mode == "baseline" else ""),
    ]
    for tc in record.tool_calls:
        res = tc.result.get("result") if "result" in tc.result else None
        if "error" in tc.result:
            outcome = f"error: {tc.result['error']}"
        elif isinstance(res, list):
            outcome = f"{len(res)} result(s)" + (
                ": " + ", ".join(str(r.get("ref", r.get("name"))) for r in res) if res else "")
        else:
            outcome = f"ticket {res.get('ref')}" if isinstance(res, dict) else "ok"
        lines.append(f"   {tc.name}({json.dumps(tc.args)}) -> {outcome}")
    if record.mode == "agent":
        lines += ["", "CONTEXT NOTES", _wrap(record.context_summary or "")]

    lines += ["", f"TRIAGE ({len(record.attempts)} attempt(s))"]
    for i, a in enumerate(record.attempts, 1):
        if a.errors:
            lines.append(f"   attempt {i} failed validation:")
            lines += [f"     - {e}" for e in a.errors]
    if result is not None:
        lines += [
            f"   Route:     {result.route.value}",
            f"   Rationale: {result.route_rationale}",
        ]
        if result.route in TASK_ROUTES:
            lines += [
                f"   Title:     {result.task_title}",
                f"   Skill:     {result.primary_skill}"
                + ("  (multiple disciplines)" if result.requires_multiple_disciplines else ""),
                "   Description:", _wrap(result.task_description, "     "),
            ]
        if result.clarifying_question:
            lines.append(f"   Question:  {result.clarifying_question}")
        if result.duplicate_of is not None:
            lines.append(f"   Duplicate of ticket {result.duplicate_of}")
        lines += [
            f"   Catalog:   {', '.join(result.catalog_refs) or '(none)'}",
            "   Caveats and pushback:", _wrap(result.caveats_and_pushback, "     "),
            "",
            f"ACTION ON APPROVAL: {planned_action(result, req)}",
        ]
    lines.append("=" * WIDTH)
    return "\n".join(lines)


def review(req: IntakeRequest, record: TraceRecord, result: TriageResult, *,
           auto: bool = False, input_fn: Callable[[str], str] = input,
           out: Callable[[str], None] = print) -> bool:
    """Print the summary and return True if approved. auto=True approves without asking."""
    out(summary(req, record, result))
    if auto:
        out("Auto-approved (--auto-approve).")
        return True
    while True:
        try:
            answer = input_fn("Approve and write to the board? [y/n] ").strip().lower()
        except EOFError:
            out("No input; treating as rejected.")
            return False
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        out("Please answer y or n.")
