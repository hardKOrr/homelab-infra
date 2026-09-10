#!/usr/bin/env python3
"""Exercise the shared recovery resolver's runtime assertions with local fixtures."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "ansible" / "tasks" / "recovery" / "resolve-method.yml"
RESTORE_JOB = ROOT / "rundeck" / "jobs" / "restore-app.yaml"
ANSIBLE = (
    os.environ.get("GATE_ANSIBLE_PLAYBOOK")
    or shutil.which("ansible-playbook")
    or str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook")
)


class RecoveryContractTests(unittest.TestCase):
    def setUp(self):
        if not Path(ANSIBLE).is_file():
            self.skipTest("ansible-playbook is not installed")
        self.native = {
            "methods": ["native"],
            "native": {
                "backup_playbook": "backup-app.yml",
                "restore_playbook": "restore-app.yml",
            },
        }

    def run_resolver(self, recovery, method=None, destination=None):
        variables = {
            "recovery_app": "fixture",
            "recovery_instance": "fixture",
            "recovery_app_config": {"recovery": recovery},
            "recovery_operation": "restore",
        }
        if method is not None:
            variables["recovery_method_requested"] = method
        if destination is not None:
            variables["recovery_destination_requested"] = destination
        playbook = [{
            "name": "recovery resolver fixture",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "tasks": [{
                "ansible.builtin.include_tasks": str(TASK),
                "vars": variables,
            }],
        }]
        with tempfile.TemporaryDirectory(prefix="recovery-contract-") as directory:
            path = Path(directory) / "fixture.yml"
            path.write_text(yaml.safe_dump(playbook), encoding="utf-8")
            return subprocess.run(
                [ANSIBLE, "-i", "localhost,", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env={**os.environ, "ANSIBLE_NOCOLOR": "1"},
                check=False,
            )

    def test_single_method_defaults_to_existing(self):
        result = self.run_resolver(self.native)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shared_resolver_accepts_new_contract_value(self):
        result = self.run_resolver(self.native, method="native", destination="new")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_multiple_methods_require_method(self):
        recovery = {
            **self.native,
            "methods": ["pbs_guest", "native"],
            "pbs_guest": {"applicable": True},
        }
        result = self.run_resolver(recovery)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("method is required", result.stdout)

    def test_undeclared_method_and_destination_fail_as_capability_errors(self):
        for method, destination in (("project_managed", "existing"), ("native", "invalid")):
            result = self.run_resolver(self.native, method=method, destination=destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("has no declared recovery capability for", result.stdout)

    def test_native_interface_is_explicit(self):
        result = self.run_resolver(
            {"methods": ["native"], "native": {"backup_playbook": "backup-app.yml"}}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing the method interface", result.stdout)

    def test_legacy_restore_job_does_not_advertise_unimplemented_new_destination(self):
        document = yaml.safe_load(RESTORE_JOB.read_text(encoding="utf-8"))
        destination = next(option for option in document[0]["options"] if option["name"] == "destination")
        self.assertEqual(destination["values"], ["existing"])
        script = document[0]["sequence"]["commands"][0]["script"]
        self.assertIn('destination=$RD_OPTION_DESTINATION', script)


if __name__ == "__main__":
    unittest.main()
