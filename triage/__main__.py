"""CLI: run one request, batch a folder, or reset the working board.

    python -m triage run --request data/intake/REQ-001.json [--no-act] [--baseline] [--auto-approve]
    python -m triage batch data/intake/ [--no-act] [--baseline] [--auto-approve]
    python -m triage reset-board
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from triage.board import JsonBoard, reset_from_seed
from triage.config import Settings, get_settings
from triage.schemas import IntakeRequest
from triage.tracing import BudgetExceeded, default_trace_path, total_spend


def _open_board(cfg: Settings) -> JsonBoard:
    if not cfg.board_path.exists():
        return reset_from_seed(cfg.seed_board_path, cfg.board_path)
    return JsonBoard(cfg.board_path)


def _load(path: Path) -> IntakeRequest:
    return IntakeRequest.model_validate_json(path.read_text(encoding="utf-8"))


def _run_paths(paths: list[Path], args: argparse.Namespace) -> int:
    from triage import pipeline   # imports google-genai via llm; keeps --help fast

    cfg = get_settings()
    board = _open_board(cfg)
    print(f"[{cfg.model_id}, thinking={cfg.thinking_level}, board={cfg.board_path.name}, "
          f"recorded spend ${total_spend():.4f} of ${cfg.max_spend_usd:.2f}]")
    failures = 0
    for path in paths:
        try:
            record = pipeline.run(_load(path), board, cfg, baseline=args.baseline,
                                  no_act=args.no_act, auto_approve=args.auto_approve or None)
        except BudgetExceeded as exc:
            print(f"\nSTOPPED: {exc}")
            return 2
        except Exception as exc:  # noqa: BLE001 - report and continue the batch
            print(f"\n{path.name}: run failed: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        failures += record.decision == "manual_review"
        u = record.usage
        print(f"\n{record.request_id}: decision={record.decision}"
              + (f" action={record.action}" if record.action else "")
              + f"  cost=${record.total_cost_usd:.4f}  latency={record.latency_s:.1f}s"
              + f"  tokens in={sum(x.prompt_tokens + x.tool_use_prompt_tokens for x in u)}"
              + f" out={sum(x.candidates_tokens + x.thoughts_tokens for x in u)}")
    print(f"\nTraces: {default_trace_path()}")
    return 1 if failures else 0


def cmd_run(args: argparse.Namespace) -> int:
    return _run_paths([Path(args.request)], args)


def cmd_batch(args: argparse.Namespace) -> int:
    paths = sorted(Path(args.dir).glob("*.json"))
    if not paths:
        print(f"No .json files in {args.dir}")
        return 1
    return _run_paths(paths, args)


def cmd_reset_board(args: argparse.Namespace) -> int:
    cfg = get_settings()
    reset_from_seed(cfg.seed_board_path, cfg.board_path)
    print(f"Reset {cfg.board_path} from {cfg.seed_board_path.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="triage", description="Analytics intake triage agent")
    sub = p.add_subparsers(dest="command", required=True)

    def add_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--no-act", action="store_true", help="Stop after triage; write nothing")
        sp.add_argument("--baseline", action="store_true",
                        help="Skip the context loop (Assignment 1 baseline)")
        sp.add_argument("--auto-approve", action="store_true",
                        help="Approve without asking (eval runs on a fresh board only)")

    run = sub.add_parser("run", help="Triage one intake request")
    run.add_argument("--request", required=True, help="Path to an intake JSON file")
    add_flags(run)
    run.set_defaults(func=cmd_run)

    batch = sub.add_parser("batch", help="Triage every .json file in a folder")
    batch.add_argument("dir", help="Folder of intake JSON files")
    add_flags(batch)
    batch.set_defaults(func=cmd_batch)

    reset = sub.add_parser("reset-board", help="Copy the seed board over the working board")
    reset.set_defaults(func=cmd_reset_board)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
