#!/usr/bin/env python3
"""Fixture contract checks for the shared PBS VM/LXC recovery route.

These tests intentionally do not contact Proxmox or PBS. They assert the mutation ordering
and execute the production identity assertions and restore-command arguments with a
recording action plugin. This bounded seam coverage is not full restore execution.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from jinja2 import Environment, StrictUndefined
from ansible.plugins.filter.core import FilterModule
import yaml

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "ansible/playbooks/maintenance/backup-guest.yml"
RESTORE = ROOT / "ansible/playbooks/maintenance/restore-guest.yml"
WAIT = ROOT / "ansible/tasks/proxmox/wait-for-task.yml"
BACKUP_JOB = ROOT / "rundeck/jobs/backup-guest.yaml"
RESTORE_JOB = ROOT / "rundeck/jobs/restore-guest.yaml"
GROUPS = ROOT / "rundeck/job-groups.yml"


def fixture_command(argv, fixture_path):
    """Record restore arguments without executing a provider command."""
    assert argv[:2] == ["pvesh", "create"]
    assert argv[2] in ("/nodes/fixture-node/qemu", "/nodes/fixture-node/lxc")
    state = json.loads(Path(fixture_path).read_text())
    state["calls"].append(argv)
    Path(fixture_path).write_text(json.dumps(state))
    return {"rc": 0, "stdout": "UPID:fixture", "stderr": "", "changed": True}


def find_yaml_value(node, key):
    """Find a key in the parsed playbook without depending on task ordering."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = find_yaml_value(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_yaml_value(value, key)
            if found is not None:
                return found
    return None


class GuestRecoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backup = BACKUP.read_text(encoding="utf-8")
        cls.restore = RESTORE.read_text(encoding="utf-8")
        cls.wait = WAIT.read_text(encoding="utf-8")

    def test_backup_checks_schedule_storage_and_waits_for_vzdump(self):
        self.assertIn("/cluster/backup", self.backup)
        self.assertIn("/storage/{{ _bg_storage }}/content", self.backup)
        self.assertIn("/nodes/{{ _bg_guest.node }}/vzdump", self.backup)
        self.assertIn("--mode", self.backup)
        self.assertIn("snapshot", self.backup)
        self.assertIn("wait-for-task.yml", self.backup)
        self.assertIn("native PBS artifact", self.backup)
        self.assertNotIn("qmrestore", self.backup)
        self.assertNotIn("pct restore", self.backup)

    def test_restore_has_backend_specific_vm_and_lxc_calls(self):
        self.assertIn('"/nodes/{{ _rg_target_node }}/qemu"', self.restore)
        self.assertIn("--archive", self.restore)
        self.assertIn('"/nodes/{{ _rg_target_node }}/lxc"', self.restore)
        self.assertIn("--ostemplate", self.restore)
        self.assertIn("--restore", self.restore)
        self.assertIn("--unique", self.restore)
        self.assertIn("--force", self.restore)
        self.assertIn("--start", self.restore)
        self.assertIn("--start\n              - '0'", self.restore)
        self.assertIn("wait-for-task.yml", self.restore)

    def test_existing_target_preflight_precedes_stop_and_restore(self):
        preflight = self.restore.index("Require readable PBS credentials")
        decryption = self.restore.index("Require artifact credentials and decryption material")
        capture = self.restore.index("Capture and verify an independent pre-restore recovery point")
        stop = self.restore.index("Stop an existing running target")
        restore = self.restore.index("Restore a VM from the native PBS artifact")
        self.assertLess(preflight, stop)
        self.assertLess(decryption, stop)
        self.assertLess(capture, stop)
        self.assertLess(stop, restore)
        self.assertIn("_rg_pre_restore_point != _rg_artifact", self.restore)
        self.assertIn("not ((_rg_targets | first).template | default(0) | bool)", self.restore)
        self.assertIn("Require the independent pre-restore point to remain available", self.restore)
        self.assertIn("Require independent pre-restore credentials and decryption material", self.restore)
        self.assertIn("failed or timed out", self.restore)
        self.assertNotIn("destroy", self.restore.lower())

    def test_new_target_is_stopped_and_identity_is_target_owned(self):
        self.assertIn("_rg_destination == 'new'", self.restore)
        self.assertIn("target_network", self.restore)
        self.assertIn("_rg_target_tags", self.restore)
        self.assertIn("_rg_destination == 'new' else '0'", self.restore)
        self.assertIn("New targets remain stopped", self.restore)
        self.assertIn("production cutover is false", self.restore)

    def test_restore_preserves_existing_onboot_and_disables_new_target(self):
        self.assertIn(
            "+ ['--tags', _rg_restore_tags, '--onboot', _rg_restore_onboot]",
            self.restore,
        )
        self.assertNotIn(
            "+ ['--tags', _rg_restore_tags, '--onboot', '0']",
            self.restore,
        )

        restore_onboot = find_yaml_value(yaml.safe_load(self.restore), "_rg_restore_onboot")
        self.assertIsInstance(restore_onboot, str)
        renderer = Environment(undefined=StrictUndefined)
        renderer.filters["from_json"] = json.loads
        for destination, prior_onboot, expected_onboot in (
            ("new", 1, "0"),
            ("existing", 1, "1"),
            ("existing", 0, "0"),
        ):
            rendered = renderer.from_string(restore_onboot).render(
                _rg_destination=destination,
                _rg_existing_config={"stdout": json.dumps({"onboot": prior_onboot})},
            )
            self.assertEqual(rendered, expected_onboot)

    def test_rundeck_exposes_shared_contract_and_lab_group(self):
        backup = yaml.safe_load(BACKUP_JOB.read_text(encoding="utf-8"))[0]
        restore = yaml.safe_load(RESTORE_JOB.read_text(encoding="utf-8"))[0]
        groups = yaml.safe_load(GROUPS.read_text(encoding="utf-8"))["jobs"]
        self.assertEqual(groups["backup-guest.yaml"], "Recover/Guests")
        self.assertEqual(groups["restore-guest.yaml"], "Recover/Guests")
        restore_options = {option["name"] for option in restore["options"]}
        self.assertTrue({"destination", "recovery_point", "pre_restore_point", "overwrite"} <= restore_options)
        self.assertEqual(backup["group"], "Recover/Guests")
        self.assertEqual(restore["group"], "Recover/Guests")
        script = restore["sequence"]["commands"][0]["script"]
        self.assertIn("restore-guest.yml", script)
        self.assertIn("destination=", script)
        self.assertIn("overwrite=", script)
        self.assertIn("target_name=", script)

class RestoreGuestSourceTests(unittest.TestCase):
    """Run owning identity tasks only, without claiming full-path recovery acceptance."""

    @classmethod
    def setUpClass(cls):
        cls.source = yaml.safe_load(RESTORE.read_text())[0]
        cls.renderer = Environment(undefined=StrictUndefined)
        cls.renderer.filters.update(FilterModule().filters())
        cls.work = tempfile.TemporaryDirectory(prefix="restore-guest-source-")
        cls.directory = Path(cls.work.name)
        plugins = cls.directory / "plugins"
        plugins.mkdir()
        (cls.directory / "guest_recovery_fixture.py").write_text(Path(__file__).read_text())
        (plugins / "fixture_command.py").write_text(
            "import os\nfrom ansible.plugins.action import ActionBase\n"
            "from guest_recovery_fixture import fixture_command\n"
            "class ActionModule(ActionBase):\n"
            "    def run(self, tmp=None, task_vars=None):\n"
            "        return fixture_command(self._task.args['argv'], os.environ['RESTORE_FIXTURE'])\n"
        )
        cls.inventory = cls.directory / "inventory.ini"
        cls.inventory.write_text("[proxmox_delegates]\nfixture-node ansible_connection=local\n")
        cls.env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("ANSIBLE_", "PROXMOX_", "RD_"))}
        cls.env.update(ANSIBLE_CONFIG=str(ROOT / "ansible/ansible.cfg"),
                       ANSIBLE_STDOUT_CALLBACK="default",
                       ANSIBLE_ACTION_PLUGINS=str(plugins),
                       ANSIBLE_LOCAL_TEMP=str(cls.directory / "tmp"),
                       PYTHONPATH=str(cls.directory),
                       RESTORE_FIXTURE=str(cls.directory / "state.json"))

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def render(self, value, **variables):
        return self.renderer.from_string(value).render(**variables)

    def test_normalization_preserves_native_timestamps_at_every_source_site(self):
        selected = self.source["vars"]["_rg_artifact"]
        capture = next(t for t in self.source["pre_tasks"] if "block" in t)["block"]
        supplied = capture[0]["ansible.builtin.set_fact"]["_rg_pre_restore_point"]
        newest = next(t for t in capture if t["name"].startswith("Select the newest"))[
            "ansible.builtin.set_fact"]["_rg_pre_restore_point"]
        for kind in ("vm", "ct"):
            native = f"backup/{kind}/501/2026-10-04T12:34:56Z"
            for prefix in ("", "pbs-fixture:"):
                with self.subTest(kind=kind, prefix=prefix):
                    point = prefix + native
                    self.assertEqual(self.render(selected, recovery_point=point), native)
                    self.assertEqual(self.render(supplied, pre_restore_point=point), native)
                    self.assertEqual(self.render(newest, _rg_pre_artifacts={
                        "stdout": json.dumps([{"volid": point, "ctime": 1}])}), native)

    def run_source(self, kind="ct", destination="existing", point=None, pre=None,
                   qualified=True, captured=False):
        import copy
        native = f"backup/{kind}/501/2026-10-04T12:34:56Z"
        target = "502" if destination == "new" else "501"
        independent = f"backup/{kind}/{target}/2026-10-04T13:45:57Z"
        prefix = "pbs-fixture:" if qualified else ""
        point = prefix + native if point is None else point
        pre = prefix + independent if pre is None else pre
        play = copy.deepcopy(self.source)
        production = play["pre_tasks"]
        shared_contract = next(task for task in production
                               if task["name"] == "Validate the shared restore contract")
        artifact_identity = next(task for task in production
                                 if task["name"] == "Validate artifact identity before touching a target")
        capture = next(task for task in production if "block" in task)["block"]
        supplied = capture[0]
        selected = next(task for task in capture if task["name"].startswith("Select the newest"))
        identity = next(task for task in capture if task["name"] == "Require an independent readable pre-restore point")
        selected.pop("when", None)
        # No credential loading, provider inventory, or full destination validation here.
        # Keep the actual identity assertions followed by actual VM/LXC restore arguments,
        # so a rejected input must fail before either mutation can be recorded.
        play["pre_tasks"] = [shared_contract, artifact_identity,
                             selected if captured else supplied, identity]
        commands = play["tasks"][0]["block"]
        play["tasks"] = [task for task in commands if task["name"] in (
            "Restore a VM from the native PBS artifact", "Restore an LXC from the native PBS artifact")]
        for task in play["tasks"]:
            task["fixture_command"] = task.pop("ansible.builtin.command")
        play["vars"].update(homelabinfra_config={"proxmox": {"node": "fixture-node"}},
                            backup_storage="pbs-fixture", source_vmid="501",
                            target_vmid=target, target_storage="local",
                            _rg_target_node="fixture-node",
                            _rg_target_type="qemu" if kind == "vm" else "lxc",
                            _rg_pre_artifacts={"stdout": json.dumps([
                                {"volid": pre, "ctime": 1}])},
                            destination=destination, recovery_point=point,
                            pre_restore_point=pre, overwrite=True)
        path = self.directory / "play.yml"
        path.write_text(yaml.safe_dump([play], sort_keys=False))
        fixture = Path(self.env["RESTORE_FIXTURE"])
        fixture.write_text(json.dumps(dict(calls=[], status="stopped", config={})))
        result = subprocess.run([str(Path(sys.executable).parent / "ansible-playbook"),
                                 "-i", str(self.inventory), str(path)], env=self.env,
                                cwd=ROOT, text=True, capture_output=True, timeout=60)
        return result, json.loads(fixture.read_text())

    def assert_refused(self, expected_task, **values):
        result, state = self.run_source(**values)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(expected_task, result.stdout.split("fatal:")[0].split("TASK [")[-1])
        self.assertFalse(any(call[1] in ("create", "set") for call in state["calls"]), state["calls"])

    def test_native_identities_reach_restore_arguments_without_timestamp_loss(self):
        for kind in ("vm", "ct"):
            for qualified in (False, True):
                for destination in ("existing", "new"):
                    with self.subTest(kind=kind, qualified=qualified, destination=destination):
                        result, state = self.run_source(kind=kind, qualified=qualified, destination=destination)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(len(state["calls"]), 1)
                        restore = state["calls"][0]
                        self.assertIn(f"pbs-fixture:backup/{kind}/501/2026-10-04T12:34:56Z", restore)
                        self.assertEqual(restore[restore.index("--unique") + 1], "1" if destination == "new" else "0")
                        self.assertEqual(restore[restore.index("--start") + 1], "0")

    def test_existing_filename_identities_remain_kind_and_source_bound(self):
        for kind, backend in (("vm", "qemu"), ("ct", "lxc")):
            point = f"pbs-fixture:backup/{kind}/501/vzdump-{backend}-501-2026_10_04-12_34_56"
            pre = f"backup/{kind}/501/vzdump-{backend}-501-2026_10_04-13_45_57"
            result, _ = self.run_source(kind=kind, point=point, pre=pre)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_refused("Validate artifact identity", point="backup/vm/501/vzdump-lxc-501-wrong")
        self.assert_refused("Validate artifact identity", point="backup/ct/501/vzdump-lxc-502-wrong")

    def test_source_identity_refuses_wrong_source_malformed_and_nested(self):
        for point in ("backup/ct/502/2026-10-04T12:34:56Z", "backup/ct/501/not-a-time",
                      "backup/ct/501/2026-10-04T12:34:56Z/nested",
                      "pbs-fixture:other:backup/ct/501/2026-10-04T12:34:56Z"):
            with self.subTest(point=point):
                self.assert_refused("Validate", point=point)

    def test_independent_point_refuses_wrong_source_kind_malformed_nested_and_same(self):
        for pre in ("backup/ct/502/2026-10-04T13:45:57Z", "backup/vm/501/2026-10-04T13:45:57Z",
                    "backup/ct/501/not-a-time", "backup/ct/501/2026-10-04T13:45:57Z/nested",
                    "backup/ct/501/2026-10-04T12:34:56Z"):
            with self.subTest(pre=pre):
                self.assert_refused("Require an independent readable pre-restore point", pre=pre)

    def test_selected_native_independent_point_preserves_timestamp(self):
        for kind in ("vm", "ct"):
            for qualified in (False, True):
                result, _ = self.run_source(kind=kind, captured=True, qualified=qualified)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
