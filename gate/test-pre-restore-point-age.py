#!/usr/bin/env python3
"""Run ansible/tasks/recovery/require-recent-pre-restore-point.yml on sample points.

A point of the target's own group taken within the hour passes; a stale, future,
malformed, other-group or selected-again point is refused.
"""

import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
task = repo / "ansible/tasks/recovery/require-recent-pre-restore-point.yml"


def point(group="wordpress", offset=timedelta()):
    return f"host/{group}/{(datetime.now(timezone.utc) + offset).strftime('%Y-%m-%dT%H:%M:%SZ')}"


def run(pre_restore_point, snapshot="host/wordpress/2026-10-01T00:00:00Z", **extra):
    with tempfile.TemporaryDirectory() as directory:
        playbook = Path(directory) / "age.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "wordpress", "restore_snapshot": snapshot,
                     "restore_pre_restore_point": pre_restore_point,
                     "recovery_pre_restore_backup_id": "wordpress", **extra},
            "tasks": [{"ansible.builtin.include_tasks": str(task)}]}]))
        return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                              env=os.environ | {"ANSIBLE_NOCOLOR": "1"}, text=True,
                              capture_output=True, check=False)


def main():
    result = run(point(offset=-timedelta(minutes=5)))
    assert result.returncode == 0, result.stdout + result.stderr
    fresh = point()
    for case, (value, extra) in {
        "two hours old": (point(offset=-timedelta(hours=2)), {}),
        "in the future": (point(offset=timedelta(minutes=5)), {}),
        "not a timestamp": ("host/wordpress/yesterday", {}),
        "another group": (point("bookstack"), {}),
        "empty": ("", {}),
        "the selected point": (fresh, {"snapshot": fresh}),
        "older than a shorter limit": (point(offset=-timedelta(minutes=10)),
                                       {"recovery_pre_restore_max_age": 300}),
    }.items():
        result = run(value, **extra)
        assert result.returncode != 0, case
        assert "taken no more than" in result.stdout, case
    print("pre-restore point age: recent own-group points pass; stale, future, malformed and foreign ones fail")


if __name__ == "__main__":
    main()
