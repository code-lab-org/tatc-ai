"""CI gate for the scoped eval.

The workflow calls ``python -m eval.ci gate`` with the model-facing
snapshot from the pull request base commit and the snapshot from the
checkout. The gate selects cases with scoped_cases.select_cases, never
runs the full suite when no case is selected, runs eval.run_eval with
the recorded HTTP cache and a bounded wall-clock budget, and classifies
the result rows:

- ``passed``: every selected case ran and passed its checks.
- ``checker_failure``: a scored case failed its checks. This blocks.
- ``infra_failure``: the harness or provider could not measure a case.
  The gate retries once within its budget; a second infrastructure
  failure is recorded as ``unmeasured`` and does not block.
- ``harness_failure``: the run produced no readable result rows, so the
  harness or its setup is broken. This blocks.
- ``skipped``: no case is selected. Gaps are reported and nothing runs.
- ``selection_error``: a changed tool has no TOOL_CASES entry, so the
  mapping table must be updated before merge. This blocks.

Exit codes for ``gate``: 0 for passed, unmeasured, and skipped; 1 for
a checker failure, a harness failure, and a selection error. The
``select`` subcommand exits 1 when the selection raises. The
``classify`` subcommand exits 0 for passed, 2 for an infrastructure
failure (retryable, so the caller decides), and 1 when a scored case
failed or the harness produced nothing.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .scoped_cases import select_cases

HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE.parent
RESULTS = HERE / "results"

# The recorded HTTP cache means one attempt is bounded by the harness
# itself: a startup, one streaming probe, and one wave of at most three
# conversations. The gate budget bounds every attempt together so two
# attempts fit the 12-minute CI job with setup headroom.
REQUEST_TIMEOUT = 120
CONVERSATION_TIMEOUT = 240
WORKERS = 3
GATE_BUDGET_SECONDS = 480.0
RETRY_RESERVE_SECONDS = 180.0
MIN_RETRY_SECONDS = 60.0

PASSED = "passed"
CHECKER_FAILURE = "checker_failure"
INFRA_FAILURE = "infra_failure"
HARNESS_FAILURE = "harness_failure"
UNMEASURED = "unmeasured"
SKIPPED = "skipped"
SELECTION_ERROR = "selection_error"

# Verdicts that must not block the merge.
PASSING = (PASSED, UNMEASURED, SKIPPED)

RunCases = Callable[..., Tuple[Optional[int], Optional[Path]]]


def load_snapshots(before: Path, after: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load the base-commit and checkout model-facing snapshots."""
    return (
        json.loads(Path(before).read_text()),
        json.loads(Path(after).read_text()),
    )


def classify(rows: List[Dict[str, Any]], expected_cases: Sequence[str]) -> str:
    """Classify eval result rows into a gate verdict.

    A scored row that failed its checks or whose checker raised is a
    checker failure and wins over infrastructure noise, because a real
    regression or a broken check must block. Rows that never ran, or
    that stopped with an infrastructure error, are an infrastructure
    failure. No rows at all means the harness never started, which
    blocks.
    """
    if not rows:
        return HARNESS_FAILURE
    if any(row.get("status") == "checker_error" for row in rows):
        return CHECKER_FAILURE
    if any(row.get("status") == "ok" and not row.get("passed") for row in rows):
        return CHECKER_FAILURE
    scored = {row.get("case") for row in rows if row.get("status") == "ok"}
    missing = [case for case in expected_cases if case not in scored]
    if missing or any(row.get("status") != "ok" for row in rows):
        return INFRA_FAILURE
    return PASSED


def read_rows(path: Path) -> List[Dict[str, Any]]:
    """Read the complete rows of one result JSONL file.

    A killed subprocess can leave a truncated last line. Keep the rows
    before it instead of losing a checker failure that already scored.
    """
    rows: List[Dict[str, Any]] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return rows


def _failed_checks(rows: List[Dict[str, Any]]) -> List[str]:
    """Names of the failed checkers, written as case:check."""
    failed = set()
    for row in rows:
        if row.get("status") != "ok" or row.get("passed"):
            continue
        for check, ok in (row.get("checks") or {}).items():
            if not ok:
                failed.add(f"{row.get('case')}:{check}")
    return sorted(failed)


def _run_eval(
    cases: Sequence[str],
    *,
    attempt: int,
    timeout_seconds: float,
) -> Tuple[Optional[int], Optional[Path]]:
    """Run the scoped eval once; return its exit code and result file.

    The subprocess is killed at ``timeout_seconds`` so no attempt can
    outlive the gate budget.
    """
    result = RESULTS / f"ci-scoped-attempt-{attempt}.jsonl"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.unlink(missing_ok=True)
    command = [
        sys.executable,
        "-m",
        "eval.run_eval",
        "--cases",
        *cases,
        "--offline",
        "--workers",
        str(WORKERS),
        "--request-timeout",
        str(REQUEST_TIMEOUT),
        "--conversation-timeout",
        str(CONVERSATION_TIMEOUT),
        "--label",
        f"ci-scoped-attempt-{attempt}",
        "--result-out",
        str(result),
    ]
    try:
        completed = subprocess.run(
            command, cwd=SERVER_DIR, check=False, timeout=max(1.0, timeout_seconds)
        )
    except subprocess.TimeoutExpired:
        return None, result
    return completed.returncode, result


def _attempt_verdict(
    returncode: Optional[int], result_path: Optional[Path], cases: Sequence[str]
) -> Tuple[str, Optional[str], List[str]]:
    """Classify one attempt; return its verdict, reason, and failed checks."""
    if result_path is None or not Path(result_path).exists():
        if returncode is None:
            return INFRA_FAILURE, "the attempt timed out before writing a result file", []
        return HARNESS_FAILURE, "the eval wrote no result file", []
    try:
        rows = read_rows(result_path)
    except OSError:
        return INFRA_FAILURE, "the result file is unreadable", []
    if not rows and returncode is None:
        return INFRA_FAILURE, "the attempt timed out before writing a complete row", []
    if returncode not in (0, None) and not rows:
        return HARNESS_FAILURE, "the eval exited before writing results", []
    verdict = classify(rows, cases)
    failed = _failed_checks(rows)
    if verdict == INFRA_FAILURE:
        return verdict, "infrastructure errors or cases that never ran", failed
    return verdict, None, failed


def _selection_payload(selection) -> Dict[str, Any]:
    return {
        "cases": list(selection.cases),
        "gaps": [{"tool": gap.tool, "reason": gap.reason} for gap in selection.gaps],
    }


def run_gate(
    before: Path,
    after: Path,
    *,
    budget_seconds: float = GATE_BUDGET_SECONDS,
    run_cases: Optional[RunCases] = None,
    log: Callable[[str], None] = print,
    summary: Optional[Path] = None,
) -> Dict[str, Any]:
    """Select, run, and classify; return the verdict payload.

    ``run_cases`` is the eval runner; tests replace it. The full suite
    never runs: the eval is invoked only with the selected case IDs.
    """
    run_cases = run_cases or _run_eval
    try:
        before_surface, after_surface = load_snapshots(before, after)
        selection = select_cases(before_surface, after_surface)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"verdict": SELECTION_ERROR, "cases": [], "gaps": [], "error": str(exc)}
        log(f"selection error: {exc}")
        log("gate: " + json.dumps(payload))
        _write_summary(summary, payload)
        return payload

    payload = _selection_payload(selection)
    attempts: List[Dict[str, Any]] = []
    log("selection: " + json.dumps(payload))
    if not selection.cases:
        for gap in selection.gaps:
            log(f"coverage gap: {gap.tool}: {gap.reason}")
        if not selection.gaps:
            log("the model-facing snapshot is unchanged for the selector")
        log("no scoped case is selected; the full suite never runs in CI")
        verdict = SKIPPED
    else:
        cases = list(selection.cases)
        log(f"running scoped cases: {', '.join(cases)}")
        deadline = time.monotonic() + budget_seconds
        attempts_run = 0
        for attempt in (1, 2):
            remaining = deadline - time.monotonic()
            if attempt > 1 and remaining < MIN_RETRY_SECONDS:
                log("no budget left for the second attempt")
                break
            if attempt == 1:
                # Leave a real retry window instead of letting the first
                # attempt spend the whole budget.
                timeout_seconds = max(1.0, remaining - RETRY_RESERVE_SECONDS)
            else:
                timeout_seconds = remaining
            attempts_run += 1
            returncode, result_path = run_cases(
                cases, attempt=attempt, timeout_seconds=timeout_seconds
            )
            verdict, reason, failed = _attempt_verdict(returncode, result_path, cases)
            attempts.append(
                {
                    "attempt": attempt,
                    "verdict": verdict,
                    "results": str(result_path) if result_path else None,
                    "failed_checks": failed,
                }
            )
            log(f"attempt {attempt}: {verdict}" + (f" ({reason})" if reason else ""))
            if verdict != INFRA_FAILURE:
                break
        if verdict == INFRA_FAILURE:
            verdict = UNMEASURED
            if attempts_run < 2:
                log(
                    "warning: the scoped eval could not be measured and the "
                    "retry did not run within the budget; this does not block"
                )
            else:
                log(
                    "warning: the scoped eval could not be measured after one "
                    "retry; this does not block, but the change is unmeasured"
                )
    payload["verdict"] = verdict
    payload["attempts"] = attempts
    log("gate: " + json.dumps(payload))
    _write_summary(summary, payload)
    return payload


def _write_summary(summary: Optional[Path], payload: Dict[str, Any]) -> None:
    """Append a short Markdown verdict for the GitHub step summary."""
    if summary is None:
        return
    lines = [f"### Scoped eval: {payload['verdict']}"]
    if payload["cases"]:
        lines.append(f"Cases: {', '.join(payload['cases'])}")
    for gap in payload["gaps"]:
        lines.append(f"Coverage gap: `{gap['tool']}` — {gap['reason']}")
    for attempt in payload.get("attempts", []):
        results = attempt.get("results") or "no result file"
        lines.append(f"Attempt {attempt['attempt']}: {attempt['verdict']} ({results})")
        if attempt.get("failed_checks"):
            lines.append("Failed checks: " + ", ".join(attempt["failed_checks"]))
    if payload["verdict"] == UNMEASURED:
        if len(payload.get("attempts", [])) < 2:
            lines.append(
                "The run is unmeasured and the retry did not run within the budget; "
                "it does not block."
            )
        else:
            lines.append(
                "The run is unmeasured after one infrastructure retry; it does not block."
            )
    with Path(summary).open("a") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    select_parser = sub.add_parser(
        "select", help="print the selected cases and gaps for two snapshots"
    )
    select_parser.add_argument("--before", type=Path, required=True, help="base snapshot")
    select_parser.add_argument(
        "--after", type=Path, required=True, help="checkout snapshot"
    )

    classify_parser = sub.add_parser("classify", help="classify one result JSONL file")
    classify_parser.add_argument("--results", type=Path, required=True)
    classify_parser.add_argument("--cases", nargs="+", required=True)

    gate_parser = sub.add_parser("gate", help="select, run, classify, and retry once")
    gate_parser.add_argument("--before", type=Path, required=True, help="base snapshot")
    gate_parser.add_argument("--after", type=Path, required=True, help="checkout snapshot")
    gate_parser.add_argument("--budget-seconds", type=float, default=GATE_BUDGET_SECONDS)
    gate_parser.add_argument("--summary", type=Path, help="append a Markdown verdict here")

    args = parser.parse_args(argv)
    if args.command == "select":
        try:
            before, after = load_snapshots(args.before, args.after)
            print(json.dumps(_selection_payload(select_cases(before, after))))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"selection error: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "classify":
        verdict = classify(read_rows(args.results), args.cases)
        print(verdict)
        if verdict in PASSING:
            return 0
        return 2 if verdict == INFRA_FAILURE else 1
    payload = run_gate(
        args.before,
        args.after,
        budget_seconds=args.budget_seconds,
        summary=args.summary,
    )
    return 0 if payload["verdict"] in PASSING else 1


if __name__ == "__main__":
    sys.exit(main())
