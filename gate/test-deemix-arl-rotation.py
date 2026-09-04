#!/usr/bin/env python3
"""Prove an ARL rotation restarts Deemix instead of silently going stale.

roles/deemix/tasks/main.yml seeds config/.arl from Vaultwarden UNCONDITIONALLY
on every deploy, because Vaultwarden — not the running container — is this
credential's source of truth (see that file's header). Deemix reads .arl once,
at process start: a task that rewrites the file without notifying the restart
handler leaves the running container answering to the session it already
loaded, so "update Vaultwarden, redeploy" (the documented recovery flow)
would silently do nothing.

This extracts the real "Seed the Deezer ARL" task and the real "Restart
deemix" handler name from the role's own YAML — not a hand-copied stand-in —
and executes the task through Ansible's own notify/handler engine three
times: first seed, an unchanged replay, and a rotation. Only the seed and the
rotation may fire the (stubbed) restart.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

repo = Path(__file__).resolve().parents[1]
tasks_file = repo / "ansible/roles/deemix/tasks/main.yml"
handlers_file = repo / "ansible/roles/deemix/handlers/main.yml"

tasks = yaml.safe_load(tasks_file.read_text())
seed_task = next((t for t in tasks if t.get("name") == "Seed the Deezer ARL"), None)
if seed_task is None:
    sys.exit(f"ERROR: {tasks_file} no longer has a 'Seed the Deezer ARL' task.")

notify = seed_task.get("notify")
if not notify:
    sys.exit(
        "ERROR: 'Seed the Deezer ARL' does not notify a handler — an ARL rotation "
        "would rewrite config/.arl without restarting the container that already "
        "loaded the old session. Add notify: Restart deemix."
    )

handlers = yaml.safe_load(handlers_file.read_text())
handler_names = {h.get("name") for h in handlers}
if notify not in handler_names:
    sys.exit(
        f"ERROR: 'Seed the Deezer ARL' notifies {notify!r}, which is not a handler "
        f"in {handlers_file} ({sorted(handler_names)})."
    )

with tempfile.TemporaryDirectory(prefix="homelab-deemix-arl-test.") as work:
    work_path = Path(work)
    config_path = work_path / "config"
    marker = work_path / "restarts.log"

    # The task dict is reused verbatim (module + args + notify) so this test breaks
    # if the real task's shape changes in a way that would also break the deploy —
    # only the handler's action is replaced, since the real one calls
    # community.docker.docker_compose_v2, which needs a live Docker daemon.
    playbook = [
        {
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "app_config": {
                    "app": {
                        "config_path": str(config_path),
                        "puid": subprocess.run(["id", "-u"], capture_output=True, text=True, check=True).stdout.strip(),
                        "pgid": subprocess.run(["id", "-g"], capture_output=True, text=True, check=True).stdout.strip(),
                    }
                },
                "_dmx_arl": "{{ dmx_test_arl }}",
            },
            "tasks": [seed_task],
            "handlers": [
                {
                    # A stub for community.docker.docker_compose_v2 (the real
                    # handler's action), which needs a live Docker daemon this
                    # gate does not have. lineinfile would be wrong here: it is
                    # itself idempotent and would not record a SECOND restart
                    # once the marker line exists, hiding the very regression
                    # this test exists to catch.
                    "name": notify,
                    "ansible.builtin.shell": f"echo restarted >> {marker}",
                    "changed_when": True,
                }
            ],
        }
    ]
    playbook_file = work_path / "play.yml"
    playbook_file.write_text(yaml.safe_dump(playbook, sort_keys=False))
    config_path.mkdir(parents=True)

    ansible_playbook = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"

    def run(arl: str) -> None:
        result = subprocess.run(
            [
                str(ansible_playbook), str(playbook_file),
                "-e", f"dmx_test_arl={arl}",
            ],
            cwd=work_path,
            env={"ANSIBLE_INVENTORY": "localhost,", "ANSIBLE_LOCALHOST_WARNING": "False",
                 "ANSIBLE_INVENTORY_UNPARSED_WARNING": "False", "HOME": str(Path.home()),
                 "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            sys.exit(f"ERROR: ansible-playbook failed:\n{result.stdout}\n{result.stderr}")

    def restart_count() -> int:
        return len(marker.read_text().splitlines()) if marker.exists() else 0

    run("cookie-a")
    after_seed = restart_count()
    if after_seed != 1:
        sys.exit(f"ERROR: first seed did not restart Deemix (count={after_seed}, expected 1).")

    run("cookie-a")
    after_replay = restart_count()
    if after_replay != 1:
        sys.exit(
            f"ERROR: an unchanged ARL restarted Deemix again (count={after_replay}, "
            "expected 1) — that would bounce the app on every no-op deploy."
        )

    run("cookie-b")
    after_rotation = restart_count()
    if after_rotation != 2:
        sys.exit(
            f"ERROR: rotating the ARL did not restart Deemix (count={after_rotation}, "
            "expected 2) — 'update Vaultwarden, redeploy' would silently keep serving "
            "the expired session."
        )

print("Deemix ARL rotation restarts the container; an unchanged ARL does not.")
