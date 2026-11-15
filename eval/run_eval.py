"""Evaluation runner: the agent config N times and the baseline M times over every request.

    python eval/run_eval.py                                  # full run: 3 agent repeats + 1 baseline
    python eval/run_eval.py --requests REQ-001,REQ-005 --tag smoke
    python eval/run_eval.py --dry-run                        # show the plan and cost estimate only

Every run uses auto-approve on a fresh copy of the seed board in a temp folder,
and the budget check runs before each one. Results go to
eval/results/[<tag>-]<mode>-<thinking>-<config_hash>.jsonl. Re-running resumes:
(request, repeat) pairs already in the file without an error are skipped.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from triage import pipeline
from triage.board import reset_from_seed
from triage.config import ROOT, Settings, get_settings
from triage.schemas import IntakeRequest, TraceRecord
from triage.tracing import RESULTS_DIR, BudgetExceeded, config_hash, total_spend

EST_COST_PER_RUN = {"agent": 0.008, "baseline": 0.004}   # from the step 6/9 live runs, rounded up


def results_path(cfg: Settings, baseline: bool, tag: str | None = None,
                 results_dir: Path | None = None) -> Path:
    mode = "baseline" if baseline else "agent"
    name = f"{mode}-{cfg.thinking_level}-{config_hash(cfg, baseline)}.jsonl"
    return (results_dir or RESULTS_DIR) / (f"{tag}-{name}" if tag else name)


def completed(path: Path) -> set[tuple[str, int]]:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = TraceRecord.model_validate_json(line)
        except ValueError:
            continue
        if r.decision != "error":
            done.add((r.request_id, r.repeat))
    return done


def load_requests(intake_dir: Path, only: list[str] | None) -> list[IntakeRequest]:
    reqs = [IntakeRequest.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(intake_dir.glob("*.json"))]
    if only:
        missing = set(only) - {r.request_id for r in reqs}
        if missing:
            sys.exit(f"Unknown request ids: {', '.join(sorted(missing))}")
        reqs = [r for r in reqs if r.request_id in only]
    return reqs


def main(argv: list[str] | None = None, cfg: Settings | None = None,
         results_dir: Path | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repeats", type=int, default=3, help="Agent repeats per request (default 3)")
    p.add_argument("--baseline-runs", type=int, default=1, help="Baseline runs per request (default 1)")
    p.add_argument("--requests", help="Comma-separated request ids (default: all)")
    p.add_argument("--intake-dir", default=str(ROOT / "data" / "intake"))
    p.add_argument("--tag", help="Prefix for the results files, e.g. smoke")
    p.add_argument("--dry-run", action="store_true", help="Print the plan and estimate, run nothing")
    args = p.parse_args(argv)

    cfg = cfg or get_settings()
    reqs = load_requests(Path(args.intake_dir), args.requests.split(",") if args.requests else None)
    plan = [(False, rep, r) for rep in range(1, args.repeats + 1) for r in reqs] + \
           [(True, rep, r) for rep in range(1, args.baseline_runs + 1) for r in reqs]
    paths = {b: results_path(cfg, b, args.tag, results_dir) for b in (False, True)}
    done = {b: completed(paths[b]) for b in (False, True)}
    jobs = [(b, rep, r) for b, rep, r in plan if (r.request_id, rep) not in done[b]]

    estimate = sum(EST_COST_PER_RUN["baseline" if b else "agent"] for b, _, _ in jobs)
    spent = total_spend()
    print(f"{cfg.model_id}, thinking={cfg.thinking_level}, rpm_limit={cfg.rpm_limit or 'off'}")
    print(f"Runs planned: {len(plan)}, already done: {len(plan) - len(jobs)}, to run: {len(jobs)}")
    print(f"Estimated cost ${estimate:.2f}; recorded spend ${spent:.4f} of ${cfg.max_spend_usd:.2f} cap")
    for b in (False, True):
        print(f"  {'baseline' if b else 'agent':<8} -> {paths[b].relative_to(ROOT) if paths[b].is_relative_to(ROOT) else paths[b]}")
    if spent + estimate > cfg.max_spend_usd:
        print("WARNING: the estimate would cross the cap; the budget check will stop the run there.")
    if args.dry_run or not jobs:
        return 0

    failures = 0
    with tempfile.TemporaryDirectory(prefix="triage-eval-") as tmp:
        run_cfg = cfg.model_copy(update={"board_path": Path(tmp) / "board.json",
                                         "drafts_dir": Path(tmp) / "drafts",
                                         "auto_approve": True})
        for i, (baseline, rep, req) in enumerate(jobs, 1):
            board = reset_from_seed(cfg.seed_board_path, run_cfg.board_path)   # fresh per run
            label = f"[{i}/{len(jobs)}] {'baseline' if baseline else 'agent'} r{rep} {req.request_id}"
            try:
                rec = pipeline.run(req, board, run_cfg, baseline=baseline, auto_approve=True,
                                   trace_path=paths[baseline], out=lambda s: None, repeat=rep)
            except BudgetExceeded as exc:
                print(f"{label}: STOPPED: {exc}")
                return 2
            except Exception as exc:  # noqa: BLE001 - record and keep going
                print(f"{label}: ERROR {type(exc).__name__}: {exc}")
                failures += 1
                continue
            final = next((a.parsed for a in reversed(rec.attempts) if a.parsed), None)
            route = final.route.value if final else "(none)"
            print(f"{label}: {route:<20} {rec.decision:<13} tools={len(rec.tool_calls)} "
                  f"${rec.total_cost_usd:.4f} {rec.latency_s:.1f}s", flush=True)

    print(f"\nDone. Errors: {failures}. Recorded spend now ${total_spend():.4f}.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
