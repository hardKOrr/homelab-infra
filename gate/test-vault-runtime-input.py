#!/usr/bin/env python3
"""Keep vault-runtime's direct and opaque stdin modes aligned."""

import base64
import json
from pathlib import Path
import subprocess
import sys

import yaml
from remove_plan import load_playbook


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "ansible/scripts/vault-runtime.py"


def main():
    items = [{
        "name": "homelab-infra/apps/canary",
        "fields": [{"name": "app_key", "value": "fixture-app-key"}],
    }]
    source = json.dumps(items)
    direct = subprocess.run(
        [sys.executable, str(RUNTIME)], input=source, text=True,
        capture_output=True, check=False,
    )
    encoded = subprocess.run(
        [sys.executable, str(RUNTIME), "--base64-stdin"],
        input=base64.b64encode(source.encode()).decode(), text=True,
        capture_output=True, check=False,
    )
    assert direct.returncode == 0, direct.stderr
    assert encoded.returncode == 0, encoded.stderr
    assert json.loads(encoded.stdout) == json.loads(direct.stdout)

    invalid = subprocess.run(
        [sys.executable, str(RUNTIME), "--base64-stdin"],
        input="not-base64%%%", text=True, capture_output=True, check=False,
    )
    assert invalid.returncode == 2
    assert "Bitwarden item input must be valid base64" in invalid.stderr
    assert "not-base64" not in invalid.stderr

    newline = subprocess.run(
        [sys.executable, str(RUNTIME), "--base64-stdin"],
        input=base64.b64encode(source.encode()).decode() + "\n", text=True,
        capture_output=True, check=False,
    )
    assert newline.returncode == 2
    assert "Bitwarden item input must be valid base64" in newline.stderr

    restore = load_playbook(ROOT / "ansible/playbooks/maintenance/restore-app.yml")[0]
    parser_task = next(
        task for task in restore["pre_tasks"]
        if task.get("name") == "Parse the refreshed Vaultwarden runtime contract"
    )
    parser_command = parser_task["ansible.builtin.command"]
    assert parser_command["stdin_add_newline"] is False
    assert parser_command["argv"][-1] == "--base64-stdin"
    assert "| b64encode" in parser_command["stdin"]

    plays = load_playbook(ROOT / "ansible/playbooks/maintenance/restore-app.yml")
    reconcile_play = next(
        play for play in plays
        if play.get("name") == "Restore App | Establish intended connections after a verified new restore"
    )
    reconcile_task = next(
        task for task in reconcile_play["tasks"]
        if task.get("name") == "Reconcile the restored target's intended connections"
    )
    assert reconcile_task["ansible.builtin.command"]["argv"][0] == (
        "{{ ansible_playbook_python | dirname }}/ansible-playbook"
    )
    print("Vault runtime stdin input checks passed")


if __name__ == "__main__":
    main()
