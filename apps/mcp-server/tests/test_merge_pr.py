import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "merge-pr.sh"
HEAD_SHA = "abc123def4567890abc123def4567890abc123de"


def install_fake_gh(tmp_path, checks_exit):
    """Stub gh so checks reports checks_exit, view reports HEAD_SHA, merge succeeds."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    log = tmp_path / "gh.log"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$GH_LOG"\n'
        'if [ "$1" = pr ] && [ "$2" = checks ]; then\n'
        '  exit "$GH_CHECKS_EXIT"\n'
        "fi\n"
        'if [ "$1" = pr ] && [ "$2" = view ]; then\n'
        '  printf "%s\\n" "$GH_HEAD"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = pr ] && [ "$2" = merge ]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["GH_LOG"] = str(log)
    env["GH_CHECKS_EXIT"] = str(checks_exit)
    env["GH_HEAD"] = HEAD_SHA
    return env, log


def run_script(env, *args):
    return subprocess.run(
        [str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "checks_exit",
    [
        pytest.param(0, id="clean"),
        pytest.param(1, id="failing"),
        pytest.param(8, id="pending"),
    ],
)
def test_merge_pr_requires_clean_checks(tmp_path, checks_exit):
    env, log = install_fake_gh(tmp_path, checks_exit)

    completed = run_script(env, "19", "--delete-branch")

    commands = log.read_text(encoding="utf-8").splitlines()
    if checks_exit:
        assert completed.returncode != 0
        assert commands == ["pr checks 19"]
    else:
        assert completed.returncode == 0, completed.stderr
        assert commands == [
            "pr checks 19",
            "pr view 19 --json headRefOid --jq .headRefOid",
            f"pr merge 19 --match-head-commit {HEAD_SHA} --merge --delete-branch",
        ]


@pytest.mark.parametrize(
    "repo_flag",
    [
        pytest.param(["--repo", "org/repo"], id="separate-value"),
        pytest.param(["--repo=org/repo"], id="equals-value"),
    ],
)
def test_merge_pr_passes_repo_to_checks_and_view(tmp_path, repo_flag):
    env, log = install_fake_gh(tmp_path, 0)

    completed = run_script(env, "19", *repo_flag)

    assert completed.returncode == 0, completed.stderr
    flag = " ".join(repo_flag)
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"pr checks 19 {flag}",
        f"pr view 19 {flag} --json headRefOid --jq .headRefOid",
        f"pr merge 19 --match-head-commit {HEAD_SHA} --merge {flag}",
    ]


def test_merge_pr_rejects_a_repo_flag_without_value():
    completed = run_script(os.environ.copy(), "19", "--repo")

    assert completed.returncode == 2
