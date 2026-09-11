#!/usr/bin/env python3
"""Check the runner/control-plane recovery contract without contacting a live lab."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "rundeck" / "RUNNER-RECOVERY.md"
BOOTSTRAP = ROOT / "rundeck" / "bootstrap-rundeck.sh"
LAB_RUN = ROOT / "ansible" / "scripts" / "lab-run.sh"
RESTORE_JOB = ROOT / "rundeck" / "jobs" / "restore-guest.yaml"
BOOTSTRAP_PLAYBOOK = ROOT / "ansible" / "playbooks" / "bootstrap.yml"
REQUIREMENTS = ROOT / "ansible" / "requirements.yml"


class RunnerRecoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runbook = RUNBOOK.read_text(encoding="utf-8")
        cls.bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        cls.lab_run = LAB_RUN.read_text(encoding="utf-8")
        cls.restore_job = yaml.safe_load(RESTORE_JOB.read_text(encoding="utf-8"))[0]
        cls.bootstrap_playbook = BOOTSTRAP_PLAYBOOK.read_text(encoding="utf-8")

    def test_backup_scope_names_persisted_runner_state_and_exclusions(self):
        required_paths = (
            "/var/lib/rundeck/data/",
            "/var/lib/rundeck/var/storage/",
            "/var/lib/rundeck/projects/",
            "/etc/rundeck/.storage-password",
            "/etc/rundeck/rundeck-config.properties",
            "/etc/rundeck/framework.properties",
            "/etc/rundeck/realm.properties",
            "/root/.rundeck-bootstrap",
            "/etc/homelab-infra/lab-run.env",
            "state/vault-mode",
            "/var/lib/rundeck/.ssh/homelab-infra.pub",
        )
        for path in required_paths:
            self.assertIn(path, self.runbook, path)
        for excluded in ("BW_SESSION", "Bitwarden CLI app-data", "temporary SSH key"):
            self.assertIn(excluded, self.runbook, excluded)
        self.assertIn("not a usable runner recovery point", self.runbook)
        for issue in ("77", "78", "80", "89", "110", "123", "93", "91", "112", "106", "104", "117"):
            self.assertIn(
                f"https://github.com/hardKOrr/homelab-infra/issues/{issue}",
                self.runbook,
                issue,
            )

    def test_independent_material_precedes_runner_and_vault(self):
        phases = (
            "### 0. Establish independent authority and a recovery point",
            "### 1. Recover PBS before depending on it",
            "### 2. Recover the runner to a new target",
            "### 3. Recover Vaultwarden without a secret/bootstrap cycle",
            "### 4. Re-establish the platform dependency order",
        )
        offsets = [self.runbook.index(phase) for phase in phases]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn("does not depend on the failed runner/vault", self.runbook)
        self.assertIn("must not be stored in Key Storage", self.runbook)

    def test_shared_runner_restore_exposes_both_destinations(self):
        options = {option["name"]: option for option in self.restore_job["options"]}
        self.assertEqual(options["destination"]["values"], ["new", "existing"])
        self.assertIn("Restore Guest", self.runbook)
        self.assertIn("destination=new", self.runbook)
        self.assertIn("Capture A, change the disposable runner", self.runbook)
        self.assertIn("source inaccessible", self.runbook)

    def test_bootstrap_records_and_can_pin_compatibility(self):
        self.assertIn("RUNDECK_PACKAGE_VERSION_PIN", self.bootstrap)
        self.assertIn("PLATFORM_SSH_KEY_FILE", self.bootstrap)
        self.assertIn("ssh-keygen -y -P ''", self.bootstrap)
        guest_start = self.bootstrap.index(
            "set -euo pipefail\n\n# The guest receives RECOVERY_SSH_KEY_PATH"
        )
        cleanup_trap = self.bootstrap.index(
            "trap cleanup_recovery_ssh_key EXIT", guest_start
        )
        locale_setup = self.bootstrap.index("# -- locale", cleanup_trap)
        self.assertLess(guest_start, cleanup_trap)
        self.assertLess(cleanup_trap, locale_setup)
        self.assertIn('"rundeck=$RUNDECK_PACKAGE_VERSION_PIN"', self.bootstrap)
        self.assertIn('cred_set RUNDECK_PACKAGE_VERSION', self.bootstrap)
        self.assertIn('cred_set RUNDECK_KEY_STORAGE_FORMAT "aes-256-gcm-v1"', self.bootstrap)
        self.assertIn("cred_set ANSIBLE_CORE_VERSION", self.bootstrap)
        self.assertIn("RUNDECK_PACKAGE_VERSION_PIN=", self.bootstrap)
        self.assertIn("ansible-core==2.18.*", self.runbook)
        self.assertIn("community.proxmox", REQUIREMENTS.read_text(encoding="utf-8"))
        self.assertIn("DEPLOY_VAULTWARDEN=0", self.bootstrap)
        self.assertIn("RUNDECK_STORAGE_PASSWORD", self.runbook)

    def test_lab_run_keeps_recovery_outside_vault_and_ordinary_seed_bypass(self):
        recovery = "playbooks/maintenance/vaultwarden-recovery.yml"
        self.assertIn(recovery, self.lab_run)
        self.assertIn('[ "$playbook" = "playbooks/maintenance/vaultwarden-recovery.yml" ] && _lab_recovery=1', self.lab_run)
        self.assertIn('"$ANSIBLE_PLAYBOOK" -i inventory/ "$playbook"', self.lab_run)
        self.assertIn("already in Vault mode; LAB_SEED_MODE cannot bypass Vaultwarden", self.lab_run)
        refresh = self.lab_run.index("# ── Refresh the checkout")
        fatal_guard = self.lab_run.index("# EVERY FATAL CHECK BELONGS BELOW THE REFRESH")
        self.assertLess(refresh, fatal_guard)

    def test_bootstrap_playbook_keeps_vault_before_dependents_and_pbs_last(self):
        vault = self.bootstrap_playbook.index("import_playbook: apps/vaultwarden.yml")
        authentik = self.bootstrap_playbook.index("import_playbook: apps/authentik.yml")
        pbs = self.bootstrap_playbook.index("import_playbook: apps/pbs.yml")
        self.assertLess(vault, authentik)
        self.assertLess(authentik, pbs)
        self.assertIn("mandatory runtime secret store", self.bootstrap_playbook)

    def test_synthetic_new_restore_does_not_need_source(self):
        source = {"revision": "A", "jobs": ["job-a"], "executions": [101], "marker": True}
        artifact = deepcopy(source)
        destination = {"revision": "empty", "jobs": [], "executions": [], "marker": False}
        source.clear()
        destination.update(deepcopy(artifact))
        self.assertEqual(destination, artifact)
        self.assertEqual(source, {}, "new-target restore model accidentally used the source")

    def test_synthetic_existing_restore_a_after_b_preserves_b_recovery_point(self):
        state_a = {"revision": "A", "jobs": ["job-a"], "executions": [101]}
        state_b = {"revision": "B", "jobs": ["job-a", "job-b"], "executions": [101, 202]}
        pre_restore_b = deepcopy(state_b)
        restored = deepcopy(state_b)
        restored.clear()
        restored.update(deepcopy(state_a))
        self.assertEqual(restored, state_a)
        self.assertNotIn("job-b", restored["jobs"])
        self.assertNotIn(202, restored["executions"])
        self.assertEqual(pre_restore_b, state_b, "the independent B recovery point was overwritten")


if __name__ == "__main__":
    unittest.main()
