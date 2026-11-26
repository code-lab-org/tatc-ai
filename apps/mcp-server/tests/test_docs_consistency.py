from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from eval import ci
from eval.cases import Clock, build_cases

REPO_ROOT = Path(__file__).resolve().parents[3]
GUIDE = (REPO_ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("seconds", "name"),
    [
        (ci.GATE_BUDGET_SECONDS, "gate budget"),
        (ci.RETRY_RESERVE_SECONDS, "retry reserve"),
        (ci.MIN_RETRY_SECONDS, "retry floor"),
    ],
)
def test_evaluation_guide_documents_gate_timings(seconds, name):
    assert f"{seconds:g} seconds" in GUIDE, f"evaluation guide is missing the {name}"


def test_evaluation_guide_documents_scoped_eval_timeout():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "scoped-eval.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    timeout = workflow["jobs"]["scoped-eval"]["timeout-minutes"]

    assert f"{timeout}-minute" in GUIDE


def test_documentation_matches_full_eval_case_count():
    case_count = len(build_cases(Clock(datetime.now(timezone.utc))))

    assert f"{case_count} cases" in GUIDE
    assert f"{case_count}-case" in README
