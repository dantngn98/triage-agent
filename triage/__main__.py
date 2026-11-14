"""CLI entry point.

Step 6 provides `run --request PATH` (context loop + triage, printed).
Step 9 adds validation, approval, actions, tracing, `batch DIR` and --auto-approve.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from triage.board import JsonBoard, reset_from_seed
from triage.config import Settings, get_settings
from triage.schemas import IntakeRequest


def _open_board(cfg: Settings) -> JsonBoard:
    if not cfg.board_path.exists():
        return reset_from_seed(cfg.seed_board_path, cfg.board_path)
    return JsonBoard(cfg.board_path)


def cmd_run(args: argparse.Namespace) -> int:
    from triage import pipeline   # imports google-genai via llm; keep --help fast

    cfg = get_settings()
    req = IntakeRequest.model_validate_json(Path(args.request).read_text(encoding="utf-8"))
    board = _open_board(cfg)
    print(f"{req.request_id}: {req.title}  [{cfg.model_id}, thinking={cfg.thinking_level}]")

    usage = []
    if args.baseline:
        notes = ""
        print("\n-- baseline: context loop skipped")
    else:
        ctx = pipeline.gather_context(req, board, cfg)
        usage += ctx.usage
        print(f"\n-- context loop: {len(ctx.usage)} turn(s), {len(ctx.tool_calls)} tool call(s)")
        for tc in ctx.tool_calls:
            res = tc.result.get("result", tc.result.get("error"))
            size = f"{len(res)} item(s)" if isinstance(res, list) else "1 item" if "result" in tc.result else "error"
            print(f"   {tc.name}({json.dumps(tc.args)}) -> {size}")
        notes = ctx.notes
        print(f"\n-- context notes\n{notes or '(empty)'}")

    out = pipeline.triage_once(req, notes, None, cfg)
    usage.append(out.usage)
    print("\n-- triage result")
    if out.parsed is not None:
        print(out.parsed.model_dump_json(indent=2))
    else:
        print(f"PARSE FAILED: {out.parse_error}\nraw: {out.raw}")

    print("\n-- usage")
    for u in usage:
        print(f"   {u.call:<8} in={u.prompt_tokens + u.tool_use_prompt_tokens:>6}  "
              f"out={u.candidates_tokens:>5}  thinking={u.thoughts_tokens:>5}  {u.latency_s:.2f}s")
    if not args.no_act:
        print("\n(approval and actions arrive in step 9; nothing was written)")
    return 0 if out.parsed is not None else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="triage", description="Analytics intake triage agent")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Triage one intake request")
    run.add_argument("--request", required=True, help="Path to an intake JSON file")
    run.add_argument("--no-act", action="store_true", help="Stop after triage; write nothing")
    run.add_argument("--baseline", action="store_true", help="Skip the context loop (Assignment 1 baseline)")
    run.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
