"""Docs and workflow text must match the eval's real gate numbers and size.

The constants come from `eval/ci.py` via `ast` so this file imports no
server dependencies and runs on the guardrails-only CI path, which
installs only pytest, pyyaml, and jsonschema (needed by `eval.cases`).
"""

import ast
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from eval.cases import Clock, build_cases

REPO_ROOT = Path(__file__).resolve().parents[3]
GUIDE = (REPO_ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
CI_PATH = REPO_ROOT / "apps" / "mcp-server" / "eval" / "ci.py"
SCOPED_EVAL_PATH = REPO_ROOT / ".github" / "workflows" / "scoped-eval.yml"

CONSTANT_NAMES = ("GATE_BUDGET_SECONDS", "RETRY_RESERVE_SECONDS", "MIN_RETRY_SECONDS")


def ci_constants():
    tree = ast.parse(CI_PATH.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in CONSTANT_NAMES:
                    found[target.id] = ast.literal_eval(node.value)
    missing = set(CONSTANT_NAMES) - set(found)
    assert not missing, f"eval/ci.py no longer defines {sorted(missing)}"
    return found


def doc_errors(guide, readme, budget, reserve, floor, timeout, case_count):
    """Return one message per way the docs drift from the eval's numbers."""
    errors = []
    gate_sentence = re.compile(
        re.escape(
            f"The gate bounds the eval to {budget:g} seconds, "
            f"reserving {reserve:g} seconds for the retry, "
            f"so two attempts fit the {timeout}-minute job"
        )
    )
    if not gate_sentence.search(guide):
        errors.append(
            "evaluation guide does not tie the gate budget, retry reserve, "
            "and job timeout together"
        )
    if not re.search(re.escape(f"less than {floor:g} seconds remain"), guide):
        errors.append("evaluation guide does not state the retry floor")
    if f"The full suite is {case_count} cases" not in guide:
        errors.append("evaluation guide does not state the full-suite case count")
    if f"The full {case_count}-case eval" not in readme:
        errors.append("README does not state the full-eval case count")
    return errors


CI_CONSTANTS = ci_constants()
BUDGET = CI_CONSTANTS["GATE_BUDGET_SECONDS"]
RESERVE = CI_CONSTANTS["RETRY_RESERVE_SECONDS"]
FLOOR = CI_CONSTANTS["MIN_RETRY_SECONDS"]
TIMEOUT = yaml.safe_load(SCOPED_EVAL_PATH.read_text(encoding="utf-8"))["jobs"][
    "scoped-eval"
]["timeout-minutes"]
CASE_COUNT = len(build_cases(Clock(datetime.now(timezone.utc))))


def test_real_documents_match_the_code():
    errors = doc_errors(GUIDE, README, BUDGET, RESERVE, FLOOR, TIMEOUT, CASE_COUNT)

    assert errors == []


@pytest.mark.parametrize(
    ("find", "replace"),
    [
        pytest.param(f"{BUDGET:g} seconds", f"{BUDGET + 1:g} seconds", id="gate-budget"),
        pytest.param(f"{RESERVE:g} seconds", f"{RESERVE + 1:g} seconds", id="retry-reserve"),
        pytest.param(f"{TIMEOUT}-minute", f"{TIMEOUT + 100}-minute", id="job-timeout"),
        pytest.param(f"{CASE_COUNT} cases", f"{CASE_COUNT + 1} cases", id="guide-case-count"),
        pytest.param(f"{CASE_COUNT}-case", f"{CASE_COUNT + 1}-case", id="readme-case-count"),
    ],
)
def test_mutated_documents_are_rejected(find, replace):
    errors = doc_errors(
        GUIDE.replace(find, replace),
        README.replace(find, replace),
        BUDGET,
        RESERVE,
        FLOOR,
        TIMEOUT,
        CASE_COUNT,
    )

    assert errors, f"the helper accepted docs mutated from {find!r} to {replace!r}"
