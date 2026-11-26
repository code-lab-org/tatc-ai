import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "merge-pr.sh"
HEAD_SHA = "abc123def4567890abc123def4567890abc123de"


def install_fake_gh(
    tmp_path,
    checks_exit,
    head_before=HEAD_SHA,
    head_after=HEAD_SHA,
    bad_checks="",
    bad_statuses="",
):
    """Stub gh: checks reports checks_exit, view reports the two head SHAs."""
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
        '  if [ -f "$GH_VIEW_COUNT" ]; then\n'
        '    printf "%s\\n" "$GH_HEAD_AFTER"\n'
        "  else\n"
        '    : > "$GH_VIEW_COUNT"\n'
        '    printf "%s\\n" "$GH_HEAD_BEFORE"\n'
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = repo ] && [ "$2" = view ]; then\n'
        '  printf "%s\\n" "$GH_REPO"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = api ]; then\n'
        '  if [ "$2" = "--paginate" ]; then path=$3; else path=$2; fi\n'
        '  case "$path" in\n'
        '    */check-runs) printf "%s" "$GH_BAD_CHECKS" ;;\n'
        '    */status) printf "%s" "$GH_BAD_STATUSES" ;;\n'
        "  esac\n"
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
    env["GH_HEAD_BEFORE"] = head_before
    env["GH_HEAD_AFTER"] = head_after
    env["GH_VIEW_COUNT"] = str(tmp_path / "view-count")
    env["GH_REPO"] = "org/repo"
    env["GH_BAD_CHECKS"] = bad_checks
    env["GH_BAD_STATUSES"] = bad_statuses
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
    view = "pr view 19 --json headRefOid --jq .headRefOid"
    if checks_exit:
        assert completed.returncode != 0
        assert commands == [view, "pr checks 19"]
    else:
        assert completed.returncode == 0, completed.stderr
        assert commands[:3] == [view, "pr checks 19", view]
        assert commands[3] == "repo view --json nameWithOwner --jq .nameWithOwner"
        assert commands[4].startswith(
            f"api --paginate repos/org/repo/commits/{HEAD_SHA}/check-runs"
        )
        assert commands[5].startswith(
            f"api --paginate repos/org/repo/commits/{HEAD_SHA}/status"
        )
        assert commands[6] == (
            f"pr merge 19 --match-head-commit {HEAD_SHA} --merge --delete-branch"
        )


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
    commands = log.read_text(encoding="utf-8").splitlines()
    flag = "--repo org/repo"
    view = f"pr view 19 {flag} --json headRefOid --jq .headRefOid"
    assert commands[:3] == [view, f"pr checks 19 {flag}", view]
    assert commands[3].startswith(
        f"api --paginate repos/org/repo/commits/{HEAD_SHA}/check-runs"
    )
    assert commands[4].startswith(
        f"api --paginate repos/org/repo/commits/{HEAD_SHA}/status"
    )
    assert commands[5] == f"pr merge 19 --match-head-commit {HEAD_SHA} --merge {flag}"


def test_merge_pr_refuses_when_the_head_moves(tmp_path):
    before = "a" * 40
    after = "b" * 40
    env, log = install_fake_gh(tmp_path, 0, head_before=before, head_after=after)

    completed = run_script(env, "19")

    assert completed.returncode != 0
    view = "pr view 19 --json headRefOid --jq .headRefOid"
    assert log.read_text(encoding="utf-8").splitlines() == [
        view,
        "pr checks 19",
        view,
    ]


def test_merge_pr_refuses_when_the_pinned_sha_has_bad_checks(tmp_path):
    env, log = install_fake_gh(tmp_path, 0, bad_checks="test")

    completed = run_script(env, "19")

    assert completed.returncode != 0
    assert "not all green" in completed.stderr
    assert not any("pr merge" in line for line in log.read_text().splitlines())


def test_merge_pr_refuses_when_the_pinned_sha_has_bad_statuses(tmp_path):
    env, log = install_fake_gh(tmp_path, 0, bad_statuses="ci/test")

    completed = run_script(env, "19")

    assert completed.returncode != 0
    assert "not all green" in completed.stderr
    assert not any("pr merge" in line for line in log.read_text().splitlines())


def test_merge_pr_paginates_the_combined_status(tmp_path):
    env, log = install_fake_gh(tmp_path, 0, bad_statuses="ci/late-page")

    completed = run_script(env, "19")

    assert completed.returncode != 0
    assert "ci/late-page" in completed.stderr
    status_calls = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("api --paginate") and "/status" in line
    ]
    assert status_calls, "the combined status call must paginate"


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["--match-head-commit", "deadbeef"], id="separate-value"),
        pytest.param(["--match-head-commit=deadbeef"], id="equals-value"),
    ],
)
def test_merge_pr_rejects_a_caller_head_pin(tmp_path, args):
    env, log = install_fake_gh(tmp_path, 0)

    completed = run_script(env, "19", *args)

    assert completed.returncode == 2
    assert not log.exists()


@pytest.mark.parametrize("flag", ["--repo", "-R"])
def test_merge_pr_rejects_an_empty_repo_value(tmp_path, flag):
    env, log = install_fake_gh(tmp_path, 0)

    completed = run_script(env, "19", flag, "")

    assert completed.returncode == 2
    assert not log.exists()


def test_merge_pr_rejects_a_repo_flag_without_value():
    completed = run_script(os.environ.copy(), "19", "--repo")

    assert completed.returncode == 2
