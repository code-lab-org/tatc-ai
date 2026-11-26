import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "merge-pr.sh"


@pytest.mark.parametrize(
    "checks_exit",
    [
        pytest.param(0, id="clean"),
        pytest.param(1, id="failing"),
        pytest.param(8, id="pending"),
    ],
)
def test_merge_pr_requires_clean_checks(tmp_path, checks_exit):
    assert SCRIPT.is_file()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "gh.log"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$GH_LOG"\n'
        'if [ "$1" = pr ] && [ "$2" = checks ]; then\n'
        '  exit "$GH_CHECKS_EXIT"\n'
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

    completed = subprocess.run(
        [str(SCRIPT), "19", "--delete-branch"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    commands = log.read_text(encoding="utf-8").splitlines()
    if checks_exit:
        assert completed.returncode != 0
        assert commands == ["pr checks 19"]
    else:
        assert completed.returncode == 0, completed.stderr
        assert commands == ["pr checks 19", "pr merge 19 --merge --delete-branch"]
