#!/usr/bin/env python3
"""Ensure a recreated Ntfy guest does not reuse a token absent from that guest."""

import copy
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
ROLE = yaml.safe_load((REPO / "ansible/roles/ntfy/tasks/main.yml").read_text())
CREATE = copy.deepcopy(next(task for task in ROLE if task.get("name") == "Create access token"))
SELECT = copy.deepcopy(next(task for task in ROLE if task.get("name") == "Select the effective access token"))


def run_case(name, recorded_token, guest_token, expected_token, should_create):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        create = copy.deepcopy(CREATE)
        create.pop("register", None)
        create.pop("ansible.builtin.command")
        create["ansible.builtin.set_fact"] = {
            "_ntfy_token_add": {"stdout": "{{ fixture_minted_token }}"},
            "ntfy_test_created": True,
        }
        select = copy.deepcopy(SELECT)
        select.pop("no_log", None)
        tasks = [
            create,
            select,
            {
                "ansible.builtin.assert": {
                    "that": [
                        "_ntfy_token == fixture_expected_token",
                        "ntfy_test_created | default(false) == fixture_should_create",
                    ]
                }
            },
        ]
        playbook = root / "ntfy-token.yml"
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "gather_facts": False,
                        "vars": {
                            "homelabinfra_infra": {
                                "notifications": {"token": recorded_token}
                            },
                            "_ntfy_existing_token": guest_token,
                            "fixture_minted_token": "tk_fixtureNew",
                            "fixture_expected_token": expected_token,
                            "fixture_should_create": should_create,
                        },
                        "tasks": tasks,
                    }
                ]
            )
        )
        inventory = root / "inventory.ini"
        inventory.write_text("[local]\nlocalhost ansible_connection=local\n")
        env = os.environ | {
            "ANSIBLE_CONFIG": str(REPO / "ansible/ansible.cfg"),
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_STDOUT_CALLBACK": "default",
        }
        result = subprocess.run(
            [str(ANSIBLE), "-i", str(inventory), str(playbook)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"{name}: token selection failed\n{output}"


def main():
    run_case(
        "fresh guest with stale saved token",
        recorded_token="tk_fixtureOld",
        guest_token="",
        expected_token="tk_fixtureNew",
        should_create=True,
    )
    run_case(
        "existing guest token wins over stale saved token",
        recorded_token="tk_fixtureOld",
        guest_token="tk_fixturePresent",
        expected_token="tk_fixturePresent",
        should_create=False,
    )
    run_case(
        "existing guest token stays idempotent",
        recorded_token="tk_fixturePresent",
        guest_token="tk_fixturePresent",
        expected_token="tk_fixturePresent",
        should_create=False,
    )
    print("Ntfy token recovery: recreated guests mint a valid token; existing guest tokens are reused")


if __name__ == "__main__":
    main()
