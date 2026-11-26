from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW_JOBS = {
    path.name: set((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("jobs", {}))
    for path in WORKFLOW_DIR.glob("*.yml")
}


def test_job_ids_are_unique_across_workflows():
    owners = {}
    for workflow, job_ids in sorted(WORKFLOW_JOBS.items()):
        for job_id in sorted(job_ids):
            assert job_id not in owners, (
                f"job {job_id!r} is defined in both {owners[job_id]} and {workflow}"
            )
            owners[job_id] = workflow


@pytest.mark.parametrize(
    ("job_id", "expected_workflow"),
    [
        ("test", "test-mcp-server.yml"),
        ("scoped-eval", "scoped-eval.yml"),
    ],
)
def test_required_jobs_have_one_workflow(job_id, expected_workflow):
    owners = {workflow for workflow, job_ids in WORKFLOW_JOBS.items() if job_id in job_ids}

    assert owners == {expected_workflow}
