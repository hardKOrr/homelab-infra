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
VALIDATE_TASK = ROOT / "ansible" / "tasks" / "recovery" / "validate-restore.yml"
RESTORE_APP = ROOT / "ansible" / "playbooks" / "maintenance" / "restore-app.yml"
RESTORE_JOB = ROOT / "rundeck" / "jobs" / "restore-app.yaml"
PDM_JOB = ROOT / "rundeck" / "jobs" / "restore-pdm.yaml"
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

    def run_tasks(self, task_file, variables):
        playbook = [{
            "name": "recovery fixture",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "tasks": [{
                "ansible.builtin.include_tasks": str(task_file),
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

    def run_validator(self, **overrides):
        variables = {
            "recovery_source_instance": "fixture-a",
            "recovery_target_instance": "fixture-b",
            "recovery_destination": "new",
            "recovery_overwrite": False,
            "recovery_point": "",
            "recovery_pre_restore_point": "",
            "recovery_target_config_present": True,
            "recovery_source_route_key": "internal/source",
            "recovery_target_route_key": "internal/target",
            "recovery_source_storage_key": "docker:/opt/fixture-a/data",
            "recovery_target_storage_key": "docker:/opt/fixture-b/data",
            "recovery_affected_scope": ["fixture-a"],
        }
        variables.update(overrides)
        return self.run_tasks(VALIDATE_TASK, variables)

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
        return self.run_tasks(TASK, variables)

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

    def test_new_destination_plan_publishes_isolation_and_effects(self):
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RESTORE PLAN", result.stdout)
        self.assertIn("PRODUCTION CUTOVER: separate operation", result.stdout)
        self.assertIn("create an isolated target", result.stdout)

    def test_new_destination_rejects_route_and_identity_collisions(self):
        route = self.run_validator(recovery_target_route_key="internal/source")
        self.assertNotEqual(route.returncode, 0)
        self.assertIn("source's production route", route.stdout)

        collision = self.run_validator(recovery_target_name_collision=True)
        self.assertNotEqual(collision.returncode, 0)
        self.assertIn("Cannot create restore target", collision.stdout)

        storage = self.run_validator(recovery_target_storage_collision=True)
        self.assertNotEqual(storage.returncode, 0)
        self.assertIn("storage mapping", storage.stdout)

    def test_existing_restore_a_after_an_isolated_new_b(self):
        new_target = self.run_validator(
            recovery_source_instance="fixture-a",
            recovery_target_instance="fixture-b",
            recovery_destination="new",
            recovery_overwrite=True,
            recovery_point="artifact-a",
            recovery_target_config_present=True,
            recovery_source_route_key="internal/a",
            recovery_target_route_key="internal/b",
            recovery_source_storage_key="docker:/data/a",
            recovery_target_storage_key="docker:/data/b",
            recovery_artifact_available=True,
        )
        self.assertEqual(new_target.returncode, 0, new_target.stdout + new_target.stderr)
        self.assertIn("destination fixture-b", new_target.stdout)

        restore_a = self.run_validator(
            recovery_source_instance="fixture-a",
            recovery_target_instance="fixture-a",
            recovery_destination="existing",
            recovery_overwrite=True,
            recovery_point="artifact-b",
            recovery_pre_restore_point="artifact-a-pre",
            recovery_source_route_key="internal/a",
            recovery_target_route_key="internal/a",
            recovery_source_storage_key="docker:/data/a",
            recovery_target_storage_key="docker:/data/a",
            recovery_artifact_available=True,
            recovery_pre_restore_available=True,
            recovery_affected_scope=["fixture-a"],
        )
        self.assertEqual(restore_a.returncode, 0, restore_a.stdout + restore_a.stderr)
        self.assertIn("replace the exact existing target", restore_a.stdout)
        self.assertIn("PRODUCTION CUTOVER: separate operation", restore_a.stdout)

    def test_existing_replacement_requires_an_independent_pre_restore_point(self):
        missing = self.run_validator(
            recovery_destination="existing",
            recovery_target_instance="fixture-a",
            recovery_overwrite=True,
            recovery_point="artifact-source",
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("independent", missing.stdout)

        valid = self.run_validator(
            recovery_destination="existing",
            recovery_target_instance="fixture-a",
            recovery_overwrite=True,
            recovery_point="artifact-source",
            recovery_pre_restore_point="artifact-target",
            recovery_artifact_available=True,
            recovery_pre_restore_available=True,
        )
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertIn("replace the exact existing target", valid.stdout)

    def test_restore_jobs_expose_both_routes_and_pre_restore_proof(self):
        document = yaml.safe_load(RESTORE_JOB.read_text(encoding="utf-8"))
        destination = next(option for option in document[0]["options"] if option["name"] == "destination")
        self.assertEqual(destination["values"], ["new", "existing"])
        names = {option["name"] for option in document[0]["options"]}
        self.assertIn("pre_restore_point", names)
        script = document[0]["sequence"]["commands"][0]["script"]
        self.assertIn('destination=$RD_OPTION_DESTINATION', script)
        self.assertIn('pre_restore_point=$RD_OPTION_PRE_RESTORE_POINT', script)

        pdm = yaml.safe_load(PDM_JOB.read_text(encoding="utf-8"))
        pdm_destination = next(option for option in pdm[0]["options"] if option["name"] == "destination")
        self.assertEqual(pdm_destination["values"], ["new", "existing"])
        pdm_names = {option["name"] for option in pdm[0]["options"]}
        self.assertIn("pre_restore_point", pdm_names)
        pdm_script = pdm[0]["sequence"]["commands"][0]["script"]
        self.assertIn("destination=" + "$" + "{RD_OPTION_DESTINATION", pdm_script)
        self.assertIn('pre_restore_point=$RD_OPTION_PRE_RESTORE_POINT', pdm_script)
        pdm_playbook = (ROOT / "ansible" / "playbooks" / "maintenance" / "restore-pdm.yml").read_text(encoding="utf-8")
        stop = pdm_playbook.index("PDM | Stop the existing target")
        destroy = pdm_playbook.index("PDM | Destroy the exact managed target")
        self.assertIn("_pdm_execute | bool", pdm_playbook[stop:destroy])
        self.assertIn("_pdm_target_vmid", pdm_playbook[stop:destroy])

    def test_new_restore_wires_only_after_kubernetes_or_docker_restore(self):
        text = RESTORE_APP.read_text(encoding="utf-8")
        self.assertIn("recovery_isolated=true", text)
        self.assertIn("recovery_isolated=false", text)
        self.assertIn("restore_pre_restore_point", text)

        plays = yaml.safe_load(text)
        play_names = [play["name"] for play in plays]
        docker_restore = play_names.index("Restore App | Restore Docker application data from PBS")
        reconcile = play_names.index("Restore App | Establish intended connections after a verified new restore")
        self.assertGreater(
            reconcile,
            docker_restore,
            "new-target wiring must run after the Docker restore play",
        )

        docker_play = plays[docker_restore]
        docker_tasks = [task.get("name", "") for task in docker_play["tasks"]]
        self.assertIn("Restore the application-owned Docker archive", docker_tasks)
        self.assertIn("Fail if Docker restore degraded", docker_tasks)
        self.assertLess(
            docker_tasks.index("Restore the application-owned Docker archive"),
            docker_tasks.index("Fail if Docker restore degraded"),
        )


if __name__ == "__main__":
    unittest.main()
