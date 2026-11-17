"""
Trace building, JSONL writing, cost calculation and the spend cap.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from triage.config import INPUT_PRICE_PER_M, OUTPUT_PRICE_PER_M, ROOT, Settings
from triage.render import PROMPTS
from triage.schemas import (
    CallUsage,
    Decision,
    IntakeRequest,
    ToolCallRecord,
    TraceRecord,
    TriageAttempt,
    TriageResult,
)

TRACES_DIR = ROOT / "traces"
RESULTS_DIR = ROOT / "eval" / "results"
SPEND_DIRS = (TRACES_DIR, RESULTS_DIR)
PROMPT_FILES = ("context.md", "triage.md", "examples.json")


class BudgetExceeded(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Cost and config identity
# --------------------------------------------------------------------------
def call_cost(u: CallUsage) -> float:
    input_tokens = u.prompt_tokens + u.tool_use_prompt_tokens
    output_tokens = u.candidates_tokens + u.thoughts_tokens
    return (input_tokens * INPUT_PRICE_PER_M + output_tokens * OUTPUT_PRICE_PER_M) / 1_000_000


def config_hash(cfg: Settings, baseline: bool = False) -> str:
    """Identifies everything that shapes model behaviour: model, settings, prompt files."""
    h = hashlib.sha256()
    h.update(json.dumps({
        "model_id": cfg.model_id,
        "thinking_level": cfg.thinking_level,
        "max_context_turns": cfg.max_context_turns,
        "baseline": baseline,
    }, sort_keys=True).encode())
    for name in PROMPT_FILES:
        h.update((PROMPTS / name).read_bytes())
    return h.hexdigest()[:12]


def new_run_id() -> str:
    return f"{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


# --------------------------------------------------------------------------
# Trace builder
# --------------------------------------------------------------------------
class Trace:
    """Builds a TraceRecord as a run progresses."""

    def __init__(self, record: TraceRecord):
        self.record = record
        self._t0 = time.perf_counter()

    @classmethod
    def start(cls, req: IntakeRequest, cfg: Settings, baseline: bool = False,
              run_id: str | None = None, repeat: int | None = None) -> "Trace":
        return cls(TraceRecord(
            run_id=run_id or new_run_id(),
            request_id=req.request_id,
            model_id=cfg.model_id,
            thinking_level=cfg.thinking_level,
            config_hash=config_hash(cfg, baseline),
            mode="baseline" if baseline else "agent",
            repeat=repeat,
            started_at=datetime.now().isoformat(timespec="seconds"),
        ))

    def add_usage(self, usage: CallUsage) -> None:
        self.record.usage.append(usage.model_copy(update={"cost_usd": round(call_cost(usage), 8)}))

    def add_tool_calls(self, calls: Iterable[ToolCallRecord]) -> None:
        self.record.tool_calls.extend(calls)

    def set_context(self, notes: str) -> None:
        self.record.context_summary = notes

    def record_attempt(self, parsed: TriageResult | None, raw: str | None,
                       errors: list[str]) -> None:
        self.record.attempts.append(TriageAttempt(raw=raw, parsed=parsed, errors=list(errors)))

    def finish(self, decision: Decision) -> TraceRecord:
        self.record.decision = decision
        self.record.total_cost_usd = round(sum(u.cost_usd for u in self.record.usage), 8)
        self.record.latency_s = round(time.perf_counter() - self._t0, 3)
        return self.record


def default_trace_path() -> Path:
    return TRACES_DIR / f"{datetime.now():%Y-%m-%d}.jsonl"


def write_trace(record: TraceRecord, path: Path | None = None) -> Path:
    """Append one JSON line; defaults to traces/<YYYY-MM-DD>.jsonl."""
    path = path or default_trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
    return path


# --------------------------------------------------------------------------
# Spend cap
# --------------------------------------------------------------------------
def total_spend(dirs: Iterable[Path] | None = None) -> float:
    """Sum total_cost_usd over every line of every .jsonl file in the given folders."""
    total = 0.0
    for d in (SPEND_DIRS if dirs is None else dirs):
        if not Path(d).is_dir():
            continue
        for f in sorted(Path(d).rglob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    total += float(json.loads(line).get("total_cost_usd") or 0)
                except (ValueError, AttributeError):
                    continue   # skip a malformed line rather than block every run
    return total


def check_budget(cfg: Settings, dirs: Iterable[Path] | None = None) -> float:
    """Raise BudgetExceeded once recorded spend reaches max_spend_usd; else return spend."""
    spent = total_spend(dirs)
    if spent >= cfg.max_spend_usd:
        raise BudgetExceeded(
            f"Recorded spend ${spent:.4f} has reached the ${cfg.max_spend_usd:.2f} cap "
            "(MAX_SPEND_USD). No further runs will start.")
    return spent
