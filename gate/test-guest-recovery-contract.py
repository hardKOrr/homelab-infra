#!/usr/bin/env python3
"""Provider-free contract checks for the shared PBS VM/LXC recovery route.

The owning playbook and shared validation tasks run against a recording command plugin,
not Proxmox or PBS. This exercises production control flow with synthetic state and does
not establish live Restore Guest acceptance.
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
    """Record provider commands without executing them."""
    state = json.loads(Path(fixture_path).read_text())
    if state.get("mode") == "restore-owning-path":
        return restore_owning_path_command(argv, fixture_path, state)
    assert argv[:2] == ["pvesh", "create"]
    assert argv[2] in ("/nodes/fixture-node/qemu", "/nodes/fixture-node/lxc")
    state["calls"].append(argv)
    Path(fixture_path).write_text(json.dumps(state))
    return {"rc": 0, "stdout": "UPID:fixture", "stderr": "", "changed": True}


def restore_owning_path_command(argv, fixture_path, state):
    """Emulate the read-only inventory and recording endpoints used by Restore Guest."""
    if argv[:2] == ["fixture", "record-plan"]:
        state["shared_validation_point"] = argv[2]
        Path(fixture_path).write_text(json.dumps(state))
        return {"rc": 0, "stdout": "", "stderr": "", "changed": False}

    state["calls"].append({"argv": argv})
    command, action = argv[0], argv[1]
    target = state["target"]
    stdout = ""
    rc = 0
    changed = False
    failed = False

    if command == "pvesh" and action == "get":
        path = argv[2]
        if path == "/cluster/resources":
            stdout = json.dumps(state["resources"])
        elif path == "/nodes/fixture-node/storage":
            stdout = json.dumps([{"storage": "local", "active": 1}])
        elif path == "/nodes/fixture-node/storage/pbs-fixture/content":
            stdout = json.dumps(state["artifacts"])
        elif path.endswith("/tasks/UPID:fixture/status"):
            stdout = json.dumps({"status": "stopped", "exitstatus": "OK"})
        elif path.endswith("/status/current"):
            stdout = json.dumps({"status": target["status"]})
        elif path.endswith("/config"):
            stdout = json.dumps(target["config"])
        else:
            rc, failed = 94, True
    elif command == "pvesm" and action == "extractconfig":
        artifact = argv[2].split(":", 1)[-1]
        readable = {row["volid"].split(":", 1)[-1] for row in state["artifacts"]}
        if artifact in readable:
            stdout = json.dumps({"fixture": "readable-configuration"})
        else:
            rc, failed = 94, True
    elif command == "pvesh" and action == "create":
        path = argv[2]
        changed = True
        state["mutations"].append(argv)
        if path.endswith("/status/stop"):
            target["status"] = "stopped"
            stdout = "UPID:fixture"
        elif path.endswith("/status/start"):
            target["status"] = "running"
            stdout = "UPID:fixture"
        elif path in ("/nodes/fixture-node/qemu", "/nodes/fixture-node/lxc"):
            if state.get("fail_restore_once") and not state.get("restore_failure_used"):
                state["restore_failure_used"] = True
                target["status"] = "stopped"
                rc, failed, stdout = 1, True, ""
            else:
                target.update(exists=True, type=path.rsplit("/", 1)[-1])
                target["status"] = "stopped"
                target["config"] = {
                    "tags": "_+lab;_source",
                    "onboot": 0,
                    "net0": "name=eth0,bridge=vmbr0",
                    "rootfs": "local-lvm:vm-501-disk-0,size=8G",
                }
                target["config"]["name" if target["type"] == "qemu" else "hostname"] = "source-name"
                stdout = "UPID:fixture"
        else:
            rc, failed = 94, True
    elif command == "pvesh" and action == "set" and argv[2].endswith("/config"):
        changed = True
        state["mutations"].append(argv)
        options = dict(zip(argv[3::2], argv[4::2]))
        for key, value in options.items():
            target["config"][key.removeprefix("--")] = value
    else:
        rc, failed = 94, True

    Path(fixture_path).write_text(json.dumps(state))
    return {
        "rc": rc,
        "stdout": stdout,
        "stderr": "fixture command refused" if failed else "",
        "changed": changed,
        "failed": failed,
    }


def replace_command_modules(node):
    """Route every command task in a copied playbook/include through the fixture."""
    if isinstance(node, dict):
        if "ansible.builtin.command" in node:
            node["fixture_command"] = node.pop("ansible.builtin.command")
        for value in node.values():
            replace_command_modules(value)
    elif isinstance(node, list):
        for value in node:
            replace_command_modules(value)


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
    """Execute the owning Restore Guest path with every provider command fixture-backed."""

    @classmethod
    def setUpClass(cls):
        cls.source = yaml.safe_load(RESTORE.read_text())[0]
        cls.renderer = Environment(undefined=StrictUndefined)
        cls.renderer.filters.update(FilterModule().filters())
        cls.work = tempfile.TemporaryDirectory(prefix="restore-guest-source-")
        cls.directory = Path(cls.work.name)
        cls.fixture_repo = cls.directory / "fixture-repo"
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
        cls.inventory.write_text(
            "[proxmox_delegates]\nfixture-node ansible_connection=local\n"
            "[all:vars]\nansible_connection=local\n"
        )
        for relative in (
            "ansible/tasks/proxmox/wait-for-task.yml",
            "ansible/tasks/recovery/validate-restore.yml",
        ):
            copied = cls.fixture_repo / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            included = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
            replace_command_modules(included)
            copied.write_text(yaml.safe_dump(included, sort_keys=False), encoding="utf-8")
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

    def initial_owning_state(
        self,
        kind="ct",
        destination="existing",
        target_kind=None,
        ownership="_+lab;_fixture",
        template=0,
        ambiguous=False,
        occupy_new=False,
        fail_restore_once=False,
    ):
        target_vmid = "501" if destination == "existing" else "502"
        target_kind = target_kind or ("qemu" if kind == "vm" else "lxc")
        source = f"backup/{kind}/501/2026-10-04T12:34:56Z"
        independent = f"backup/{kind}/{target_vmid}/2026-10-04T13:45:57Z"
        exists = destination == "existing" or occupy_new
        resource = {
            "vmid": int(target_vmid),
            "type": target_kind,
            "node": "fixture-node",
            "tags": ownership,
            "template": template,
        }
        resources = [resource, dict(resource)] if ambiguous else ([resource] if exists else [])
        artifacts = [{"volid": f"pbs-fixture:{source}", "ctime": 1}]
        if destination == "existing":
            artifacts.append({"volid": f"pbs-fixture:{independent}", "ctime": 2})
        config = {
            "tags": ownership,
            "onboot": 1,
            "net0": "name=eth0,bridge=vmbr0,link_down=0",
            "rootfs": f"local-lvm:vm-{target_vmid}-disk-0,size=8G",
        }
        config["name" if target_kind == "qemu" else "hostname"] = "fixture-guest"
        return {
            "mode": "restore-owning-path",
            "destination": destination,
            "target_vmid": target_vmid,
            "source_artifact": source,
            "independent_artifact": independent,
            "artifacts": artifacts,
            "resources": resources,
            "target": {
                "exists": exists,
                "type": target_kind,
                "status": "running" if destination == "existing" else "stopped",
                "config": config if exists else {},
            },
            "calls": [],
            "mutations": [],
            "fail_restore_once": fail_restore_once,
            "restore_failure_used": False,
        }

    def run_owning_path(
        self,
        kind="ct",
        destination="existing",
        target_kind=None,
        ownership="_+lab;_fixture",
        template=0,
        ambiguous=False,
        occupy_new=False,
        fail_restore_once=False,
        same_pre_restore_point=False,
        target_name=None,
        state=None,
    ):
        import copy

        state = copy.deepcopy(state) if state is not None else self.initial_owning_state(
            kind=kind,
            destination=destination,
            target_kind=target_kind,
            ownership=ownership,
            template=template,
            ambiguous=ambiguous,
            occupy_new=occupy_new,
            fail_restore_once=fail_restore_once,
        )
        target_vmid = state["target_vmid"]
        target_kind = state["target"]["type"]
        source = state["source_artifact"]
        selected = f"pbs-fixture:{source}"
        independent = (
            selected if same_pre_restore_point else f"pbs-fixture:{state['independent_artifact']}"
        )
        destination = state["destination"]
        target_name = target_name or (f"restore-{target_vmid}" if destination == "new" else "fixture-guest")

        play = copy.deepcopy(self.source)
        play["pre_tasks"] = [
            task for task in play["pre_tasks"]
            if task.get("name") not in ("Load platform vars", "Register Proxmox delegation targets")
        ]
        replace_command_modules(play)
        play["vars"]["homelabinfra_config"] = {"proxmox": {"node": "fixture-node"}}
        play["tasks"].insert(0, {
            "name": "Record the shared recovery plan for the fixture",
            "fixture_command": {
                "argv": ["fixture", "record-plan", "{{ recovery_plan.recovery_point }}"],
            },
        })
        operator_inputs = {
            "method": "pbs_guest",
            "backup_storage": "pbs-fixture",
            "source_vmid": "501",
            "target_vmid": target_vmid,
            "target_storage": "local",
            "target_node": "fixture-node",
            "destination": destination,
            "recovery_point": selected,
            "pre_restore_point": independent if destination == "existing" else "",
            "overwrite": True,
            "target_name": target_name,
            "target_network": "name=eth0,bridge=vmbr0,link_down=1,tag=27",
            "target_tags": "_+lab;_restore-fixture",
            "restore_timeout": 60,
        }
        play_path = self.fixture_repo / "ansible/playbooks/maintenance/restore-guest.yml"
        play_path.parent.mkdir(parents=True, exist_ok=True)
        play_path.write_text(yaml.safe_dump([play], sort_keys=False), encoding="utf-8")
        inputs_path = self.directory / "operator-inputs.json"
        inputs_path.write_text(json.dumps(operator_inputs), encoding="utf-8")
        fixture = Path(self.env["RESTORE_FIXTURE"])
        fixture.write_text(json.dumps(state), encoding="utf-8")
        ansible = str(Path(sys.executable).parent / "ansible-playbook")
        result = subprocess.run(
            [ansible, "-i", str(self.inventory), str(play_path), "-e", "@" + str(inputs_path)],
            env=self.env,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
        )
        return result, json.loads(fixture.read_text(encoding="utf-8"))

    def run_assertion_task(self, task, variables):
        import copy

        play = {
            "hosts": "localhost",
            "gather_facts": False,
            "vars": variables,
            "tasks": [copy.deepcopy(task)],
        }
        path = self.directory / "assertion.yml"
        path.write_text(yaml.safe_dump([play], sort_keys=False), encoding="utf-8")
        ansible = str(Path(sys.executable).parent / "ansible-playbook")
        return subprocess.run(
            [ansible, "-i", "localhost,", "-c", "local", str(path)],
            env=self.env,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
        )

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

    def test_actual_owning_path_accepts_matching_vm_and_lxc_backends(self):
        for kind, backend in (("vm", "qemu"), ("ct", "lxc")):
            with self.subTest(kind=kind, backend=backend):
                task = next(
                    task for task in self.source["pre_tasks"]
                    if task.get("name") == "Validate source and destination backend match"
                )
                result = self.run_assertion_task(task, {
                    "_rg_destination": "existing",
                    "_rg_target_type": backend,
                    "_rg_targets": [{"type": backend}],
                })
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_owning_path_refuses_mismatched_backend_before_mutation(self):
        for kind, backend in (("vm", "lxc"), ("ct", "qemu")):
            with self.subTest(kind=kind, backend=backend):
                result, state = self.run_owning_path(kind=kind, target_kind=backend)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Validate source and destination backend match", result.stdout)
                self.assertEqual(state["mutations"], [])

    def test_actual_owning_path_refuses_unowned_template_ambiguous_and_occupied_targets(self):
        cases = (
            {"ownership": "unmanaged"},
            {"template": 1},
            {"ambiguous": True},
            {"destination": "new", "occupy_new": True},
        )
        for values in cases:
            with self.subTest(**values):
                result, state = self.run_owning_path(**values)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Require a free new destination or owned existing destination", result.stdout)
                self.assertEqual(state["mutations"], [])

    def test_actual_existing_restore_reads_independent_point_and_preserves_onboot(self):
        result, state = self.run_owning_path()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state["shared_validation_point"], state["source_artifact"])
        self.assertEqual(state["target"]["status"], "running")
        self.assertEqual(state["target"]["config"]["onboot"], "1")
        extracted = [
            call["argv"][2]
            for call in state["calls"]
            if call["argv"][:2] == ["pvesm", "extractconfig"]
        ]
        self.assertEqual(len(extracted), 2)
        self.assertNotEqual(*extracted)
        self.assertTrue(any("--onboot" in call and call[call.index("--onboot") + 1] == "1"
                            for call in state["mutations"]))
        self.assertIn(state["independent_artifact"], [
            row["volid"].split(":", 1)[-1] for row in state["artifacts"]
        ])

    def test_actual_new_restore_stays_stopped_and_target_isolated(self):
        result, state = self.run_owning_path(destination="new")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state["target"]["status"], "stopped")
        config = state["target"]["config"]
        self.assertEqual(config["onboot"], "0")
        self.assertEqual(config["hostname"], "restore-502")
        self.assertEqual(config["tags"], "_+lab;_restore-fixture")
        self.assertIn("link_down=1", config["net0"])
        self.assertFalse(any(call["argv"][2].endswith("/status/start")
                             for call in state["calls"]
                             if call["argv"][:2] == ["pvesh", "create"]))
        restore = next(call["argv"] for call in state["calls"]
                       if call["argv"][:2] == ["pvesh", "create"]
                       and call["argv"][2] == "/nodes/fixture-node/lxc")
        self.assertEqual(restore[restore.index("--unique") + 1], "1")
        self.assertEqual(restore[restore.index("--start") + 1], "0")

    def test_actual_shared_validation_keeps_independent_point_and_isolation_refusals(self):
        same_point, same_state = self.run_owning_path(same_pre_restore_point=True)
        self.assertNotEqual(same_point.returncode, 0, same_point.stdout + same_point.stderr)
        self.assertIn("Require an independent readable pre-restore point", same_point.stdout)
        self.assertEqual(same_state["mutations"], [])

        aliased_name, new_state = self.run_owning_path(destination="new", target_name="501")
        self.assertNotEqual(aliased_name.returncode, 0, aliased_name.stdout + aliased_name.stderr)
        self.assertIn("Require a different target for a new destination", aliased_name.stdout)
        self.assertEqual(new_state["mutations"], [])

    def test_shared_validator_compares_normalized_point_under_extra_vars(self):
        selected = "backup/ct/501/2026-10-04T12:34:56Z"
        play = [{
            "hosts": "localhost",
            "gather_facts": False,
            "tasks": [{
                "name": "Validate normalized restore artifact with provider-free fixture",
                "ansible.builtin.include_tasks": str(
                    self.fixture_repo / "ansible/tasks/recovery/validate-restore.yml"
                ),
                "vars": {
                    "recovery_source_instance": "501",
                    "recovery_target_instance": "501",
                    "recovery_destination": "existing",
                    "recovery_overwrite": True,
                    "recovery_validation_point": selected,
                    "recovery_pre_restore_point": selected,
                    "recovery_artifact_available": True,
                    "recovery_pre_restore_available": True,
                    "recovery_affected_scope": ["501"],
                },
            }],
        }]
        play_path = self.directory / "shared-validator-precedence.yml"
        play_path.write_text(yaml.safe_dump(play, sort_keys=False), encoding="utf-8")
        inputs_path = self.directory / "shared-validator-inputs.json"
        inputs_path.write_text(
            json.dumps({"recovery_point": f"pbs-fixture:{selected}"}), encoding="utf-8"
        )
        ansible = str(Path(sys.executable).parent / "ansible-playbook")
        result = subprocess.run(
            [ansible, "-i", "localhost,", str(play_path), "-e", "@" + str(inputs_path)],
            env=self.env,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Recovery | Require an independent pre-restore recovery point", result.stdout)

    def test_actual_restore_failure_retains_point_and_retry_succeeds(self):
        failed, state = self.run_owning_path(fail_restore_once=True)
        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self.assertIn("Report a failed or partial guest restore without destructive cleanup", failed.stdout)
        self.assertIn(state["independent_artifact"], [
            row["volid"].split(":", 1)[-1] for row in state["artifacts"]
        ])
        self.assertEqual(state["target"]["status"], "stopped")

        retried, state = self.run_owning_path(state=state)
        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        self.assertEqual(state["target"]["status"], "stopped")
        self.assertTrue(state["restore_failure_used"])
        self.assertEqual(state["target"]["config"]["onboot"], "1")
        self.assertIn(state["independent_artifact"], [
            row["volid"].split(":", 1)[-1] for row in state["artifacts"]
        ])

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
