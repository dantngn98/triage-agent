"""Context loop -> triage -> validate -> approve -> act.

Step 6 provides the context loop and the triage call; step 9 adds run()
with validation, the approval gate, actions and tracing around them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from triage import llm
from triage.board import Board
from triage.config import Settings
from triage.render import render_request, render_triage
from triage.schemas import CallUsage, IntakeRequest, ToolCallRecord
from triage.tools import dispatch_read, read_tools


@dataclass
class ContextOutcome:
    notes: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    usage: list[CallUsage] = field(default_factory=list)


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
