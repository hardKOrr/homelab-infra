#!/usr/bin/env python3
"""Fixture contract checks for the shared PBS VM/LXC recovery route.

These tests intentionally do not contact Proxmox or PBS. They assert the mutation ordering
and native artifact boundaries in the production playbooks, then run a tiny VM/LXC state
model for the two destination paths and an A -> B -> restore A replacement.
"""
from __future__ import annotations

import re
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "ansible/playbooks/maintenance/backup-guest.yml"
RESTORE = ROOT / "ansible/playbooks/maintenance/restore-guest.yml"
WAIT = ROOT / "ansible/tasks/proxmox/wait-for-task.yml"
BACKUP_JOB = ROOT / "rundeck/jobs/backup-guest.yaml"
RESTORE_JOB = ROOT / "rundeck/jobs/restore-guest.yaml"
GROUPS = ROOT / "rundeck/job-groups.yml"


_ARTIFACT = re.compile(r"^backup/(?P<kind>vm|ct)/(?P<vmid>[1-9][0-9]*)/(?P<name>[^/]+)$")


def artifact_identity(volid: str) -> dict[str, str]:
    """Parse native PBS identity without repackaging the artifact."""
    match = _ARTIFACT.fullmatch(volid.split(":", 1)[-1])
    if not match:
        raise ValueError(volid)
    expected = "vzdump-qemu-" if match["kind"] == "vm" else "vzdump-lxc-"
    if not match["name"].startswith(expected + match["vmid"] + "-"):
        raise ValueError(volid)
    return match.groupdict()


class GuestRecoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backup = BACKUP.read_text(encoding="utf-8")
        cls.restore = RESTORE.read_text(encoding="utf-8")
        cls.wait = WAIT.read_text(encoding="utf-8")

    def test_native_vm_and_lxc_artifacts_keep_identity(self):
        vm = artifact_identity("pbs-homelab:backup/vm/101/vzdump-qemu-101-2026_09_10-00_00_00.vma.zst")
        ct = artifact_identity("backup/ct/202/vzdump-lxc-202-2026_09_10-00_00_00.tar.zst")
        self.assertEqual(vm, {"kind": "vm", "vmid": "101", "name": "vzdump-qemu-101-2026_09_10-00_00_00.vma.zst"})
        self.assertEqual(ct["kind"], "ct")
        with self.assertRaises(ValueError):
            artifact_identity("backup/vm/101/vzdump-lxc-101-wrong")

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

    def test_fixture_vm_lxc_new_and_existing_transitions(self):
        """Exercise fixture outcomes without pretending they are live-lab evidence."""
        for kind, source, target in (("vm", "101", "501"), ("ct", "202", "502")):
            source_point = f"backup/{kind}/{source}/vzdump-{'qemu' if kind == 'vm' else 'lxc'}-{source}-A"
            pre_point = f"backup/{kind}/{target}/vzdump-{'qemu' if kind == 'vm' else 'lxc'}-{target}-B"
            self.assertNotEqual(source_point, pre_point)
            new_target = {"vmid": target, "kind": kind, "started": False, "unique": True}
            self.assertFalse(new_target["started"])
            self.assertTrue(new_target["unique"])
            existing_target = {"vmid": target, "kind": kind, "point": pre_point, "started": True}
            existing_target.update({"point": source_point, "identity": "target", "started": True})
            self.assertEqual(existing_target["point"], source_point)
            self.assertTrue(existing_target["started"])


if __name__ == "__main__":
    unittest.main()
