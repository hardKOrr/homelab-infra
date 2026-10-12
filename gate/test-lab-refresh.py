#!/usr/bin/env python3
"""Ordinary lab-run executions must not move the shared runner checkout."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
lab_run = repo / "ansible" / "scripts" / "lab-run.sh"
recovery_playbook = "playbooks/maintenance/vaultwarden-recovery.yml"


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True,
                            check=False)
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def clean_env(extra: dict[str, str]) -> dict[str, str]:
    base = {key: value for key, value in os.environ.items()
            if not key.startswith(("LAB_", "BW_", "ANSIBLE_VAULT"))}
    return base | extra


def wait_until_started(processes: list[subprocess.Popen[str]], paths: list[Path]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(path.exists() for path in paths):
            return
        for process in processes:
            if process.poll() is not None:
                out, err = process.communicate()
                raise AssertionError(f"lab-run exited before starting the playbook:\n{out}\n{err}")
        time.sleep(0.01)
    raise AssertionError("lab-run did not start every playbook within 10 seconds")


def run_job(test_dir: Path, run_id: str, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["bash", str(lab_run), recovery_playbook],
        env=clean_env(env | {"LAB_TEST_DIR": str(test_dir), "LAB_TEST_RUN_ID": run_id}),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def check_bootstrap_leaves_refresh_unset() -> None:
    bootstrap = (repo / "rundeck" / "bootstrap-rundeck.sh").read_text(encoding="utf-8")
    start = bootstrap.index('cat > "$LAB_ETC/lab-run.env" <<EOF')
    end = bootstrap.index("\nEOF\n", start)
    env_template = bootstrap[start:end]
    assert not any(line.startswith("LAB_REFRESH=") for line in env_template.splitlines()), \
        "bootstrap must leave LAB_REFRESH unset"


def check_jobs_stay_pinned_during_push(work: Path) -> None:
    origin = work / "origin.git"
    runner = work / "runner"
    publisher = work / "publisher"
    git("init", "-q", "--bare", str(origin))
    git("--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main")
    git("init", "-q", str(runner))
    git("-C", str(runner), "symbolic-ref", "HEAD", "refs/heads/main")
    git("-C", str(runner), "config", "user.name", "Lab Refresh Test")
    git("-C", str(runner), "config", "user.email", "lab-refresh-test@example.invalid")
    (runner / "revision.txt").write_text("before push\n", encoding="utf-8")
    git("-C", str(runner), "add", "revision.txt")
    git("-C", str(runner), "commit", "-qm", "initial runner revision")
    initial_sha = git("-C", str(runner), "rev-parse", "HEAD")
    git("-C", str(runner), "remote", "add", "origin", str(origin))
    git("-C", str(runner), "push", "-qu", "origin", "main")
    git("clone", "-q", str(origin), str(publisher))
    git("-C", str(publisher), "config", "user.name", "Lab Refresh Test")
    git("-C", str(publisher), "config", "user.email", "lab-refresh-test@example.invalid")

    (runner / "ansible").symlink_to(repo / "ansible", target_is_directory=True)
    (runner / "config").mkdir()
    state = work / "state"
    state.mkdir()
    venv = work / "venv"
    (venv / "bin").mkdir(parents=True)
    python_stub = venv / "bin" / "python3"
    python_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python_stub.chmod(0o755)

    test_dir = work / "runs"
    test_dir.mkdir()
    ansible_stub = venv / "bin" / "ansible-playbook"
    ansible_stub.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'repo="${LAB_REPO:?}"\n'
        'test_dir="${LAB_TEST_DIR:?}"\n'
        'run_id="${LAB_TEST_RUN_ID:?}"\n'
        'cat "$repo/revision.txt" > "$test_dir/$run_id.before"\n'
        'touch "$test_dir/$run_id.started"\n'
        'while [ ! -e "$test_dir/$run_id.release" ]; do sleep 0.01; done\n'
        'cat "$repo/revision.txt" > "$test_dir/$run_id.after"\n',
        encoding="utf-8",
    )
    ansible_stub.chmod(0o755)

    env_file = work / "lab-run.env"
    # This matches an older bootstrapped runner file. lab-run must ignore the stale opt-in;
    # Reimport Jobs still reads this file directly for its explicit checkout update path.
    env_file.write_text(
        f"LAB_REPO={runner}\nLAB_VENV={venv}\nLAB_BRANCH=main\n"
        "LAB_REFRESH=1\nLAB_DOCTOR=0\n",
        encoding="utf-8",
    )
    common = {
        "LAB_REPO": str(runner),
        "LAB_VENV": str(venv),
        "LAB_BRANCH": "main",
        "LAB_ENV_FILE": str(env_file),
        "LAB_STATE_DIR": str(state),
        "LAB_OPTION_DIR": str(work / "option-lists"),
        "LAB_SEED_MODE": "1",
    }

    processes = [run_job(test_dir, run_id, common) for run_id in ("first", "second")]
    try:
        wait_until_started(processes, [test_dir / "first.started", test_dir / "second.started"])
        assert all((test_dir / f"{run_id}.before").read_text(encoding="utf-8") == "before push\n"
                   for run_id in ("first", "second")), "jobs must start from the runner revision"

        (publisher / "revision.txt").write_text("after push\n", encoding="utf-8")
        git("-C", str(publisher), "add", "revision.txt")
        git("-C", str(publisher), "commit", "-qm", "push while jobs run")
        git("-C", str(publisher), "push", "-q", "origin", "main")
        assert (runner / "revision.txt").read_text(encoding="utf-8") == "before push\n", \
            "pushing a commit must not alter the shared runner checkout"

        for run_id in ("first", "second"):
            (test_dir / f"{run_id}.release").touch()
        for process in processes:
            out, err = process.communicate(timeout=10)
            assert process.returncode == 0, f"concurrent lab-run failed:\n{out}\n{err}"
        for run_id in ("first", "second"):
            assert (test_dir / f"{run_id}.after").read_text(encoding="utf-8") == "before push\n", \
                f"push changed files used by {run_id}"
        assert git("-C", str(runner), "rev-parse", "HEAD") == initial_sha, \
            "ordinary jobs must leave the runner checkout pinned"
    finally:
        for run_id in ("first", "second"):
            (test_dir / f"{run_id}.release").touch(exist_ok=True)
        for process in processes:
            if process.poll() is None:
                process.communicate(timeout=10)

    # The caller can still opt one execution into the update path explicitly.
    (test_dir / "refresh.release").touch()
    refresh = run_job(test_dir, "refresh", common | {"LAB_REFRESH": "1"})
    out, err = refresh.communicate(timeout=10)
    assert refresh.returncode == 0, f"explicit refresh failed:\n{out}\n{err}"
    assert "refreshing" in out, f"LAB_REFRESH=1 must opt in to refresh:\n{out}"
    assert (test_dir / "refresh.before").read_text(encoding="utf-8") == "after push\n", \
        "explicit refresh must run from the fetched branch"
    assert git("-C", str(runner), "rev-parse", "HEAD") != initial_sha


def main() -> int:
    check_bootstrap_leaves_refresh_unset()
    with tempfile.TemporaryDirectory(prefix="homelab-lab-refresh.") as directory:
        check_jobs_stay_pinned_during_push(Path(directory))
    print("ok   lab refresh stays opt-in across concurrent jobs and a push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
