#!/usr/bin/env python3
"""n8n-specific assertions layered on the shared recovery acceptance fixture."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

import yaml

from recovery_acceptance import (
    MethodCase,
    ProtocolFailure,
    capture,
    recover_preserved,
    restore_existing,
    restore_new,
    new_fixture,
    run_negative_cases,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "ansible/vars/app-defaults/n8n.yml"
ROLE_MAIN = ROOT / "ansible/roles/n8n/tasks/main.yml"
BACKUP = ROOT / "ansible/roles/n8n/tasks/backup.yml"
RESTORE = ROOT / "ansible/roles/n8n/tasks/restore.yml"
PLAYBOOK = ROOT / "ansible/playbooks/apps/n8n.yml"
RESTORE_DISPATCH = ROOT / "ansible/playbooks/maintenance/restore-app.yml"
DEPLOY_JOB = ROOT / "rundeck/jobs/deploy-n8n.yaml"
SPEC = ROOT / "docs/specs/n8n-recovery.md"


class N8nRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))["n8n_defaults"]
        cls.role_main = ROLE_MAIN.read_text(encoding="utf-8")
        cls.backup = BACKUP.read_text(encoding="utf-8")
        cls.restore = RESTORE.read_text(encoding="utf-8")
        cls.playbook = PLAYBOOK.read_text(encoding="utf-8")
        cls.restore_dispatch = RESTORE_DISPATCH.read_text(encoding="utf-8")
        cls.deploy_job = DEPLOY_JOB.read_text(encoding="utf-8")
        cls.spec = SPEC.read_text(encoding="utf-8")
        cls.case = MethodCase(
            product="n8n",
            method="native",
            kind="application",
            version="1.104.2",
            artifact_prefix="fixture/native/n8n",
            assertions=("workflow-state", "credential-state", "encryption-key", "database-connection"),
            source_file="ansible/playbooks/maintenance/restore-app.yml",
        )

    @staticmethod
    def n8n_state(marker: str, *, b_only: bool = False) -> dict[str, object]:
        workflows = {
            "workflow-primary": {
                "name": "Fixture workflow",
                "published": True,
                "marker": marker,
            }
        }
        credentials = {"provider-fixture": "encrypted-credential-row"}
        if b_only:
            workflows["workflow-b-only"] = {
                "name": "B-only workflow",
                "published": False,
                "marker": "B",
            }
            credentials["provider-b-only"] = "encrypted-b-only-row"
        return {
            "workflows": workflows,
            "credentials": credentials,
            "encryption_key": "vault-only-fixture-key",
            "database": {"name": "n8n", "marker": marker},
        }

    def test_declares_the_native_database_data_and_key_boundary(self):
        self.assertEqual(self.defaults["recovery"]["methods"], ["native"])
        self.assertEqual(
            self.defaults["recovery"]["native"],
            {"backup_playbook": "backup-app.yml", "restore_playbook": "restore-app.yml"},
        )
        self.assertTrue(self.defaults["backup"]["application_consistent"])
        self.assertEqual(self.defaults["backup"]["postgres_client_image"], "postgres:16-alpine")
        self.assertIn("vault_item_secret_fields: [encryption_key, database_password]", self.role_main)
        self.assertIn("../../../tasks/backup/resolve-pbs-target.yml", self.backup)
        self.assertIn("database.pxar", self.backup)
        self.assertIn("data.pxar", self.backup)
        self.assertIn("pg_dump", self.backup)
        self.assertIn("pg_restore", self.restore)
        self.assertIn("n8n.dump", self.restore)
        self.assertIn("restore_source_encryption_key", self.restore)
        self.assertIn("upsert-item.yml", self.restore)
        self.assertIn("left stopped", self.restore.lower())
        self.assertIn("recovery_isolated", self.playbook)
        self.assertIn("Skip external wiring while the restore target is isolated", self.playbook)
        self.assertIn("_ra_app == 'n8n'", self.restore_dispatch)
        self.assertIn("restore_source_encryption_key", self.restore_dispatch)
        self.assertIn("native application recovery unit", self.deploy_job)
        self.assertIn("project_managed", self.spec)
        self.assertIn("pbs_guest", self.spec)
        self.assertIn("host/<backup_id>", self.spec)

    def test_new_restore_recovers_workflows_credentials_and_key_without_source(self):
        source = new_fixture(self.case, "n8n-source")
        source.state = self.n8n_state("A")
        source.external_state = {"provider-credential": "required-external-input"}
        point = capture(self.case, source, "A")
        source_state_before = deepcopy(source.state)
        source.active = False
        source.reachable = False

        target = new_fixture(self.case, "n8n-new", "EMPTY")
        target.active = False
        result = restore_new(self.case, source, target, point)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point.state)
        self.assertEqual(target.state["workflows"]["workflow-primary"]["marker"], "A")
        self.assertEqual(target.state["credentials"]["provider-fixture"], "encrypted-credential-row")
        self.assertEqual(target.state["encryption_key"], "vault-only-fixture-key")
        self.assertEqual(target.connections["route"], target.route)
        self.assertEqual(target.identity, "n8n-new")
        self.assertTrue(target.active and target.complete)
        self.assertFalse(source.reachable)
        self.assertEqual(source.state, source_state_before)

    def test_existing_restore_is_a_to_b_to_a_and_retry_uses_retained_b(self):
        target = new_fixture(self.case, "n8n-existing", "A")
        target.state = self.n8n_state("A")
        point_a = capture(self.case, target, "A")
        target.state = self.n8n_state("B", b_only=True)
        point_b = capture(self.case, target, "B")

        result = restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point_a.state)
        self.assertNotIn("workflow-b-only", target.state["workflows"])
        self.assertNotIn("provider-b-only", target.state["credentials"])
        self.assertNotEqual(point_a.artifact_id, point_b.artifact_id)

        target.state = deepcopy(point_b.state)
        target.active = True
        target.complete = True
        with self.assertRaisesRegex(ProtocolFailure, "after replacement"):
            restore_existing(
                self.case,
                target,
                point_a,
                point_b,
                failure_stage="after_replace",
            )
        self.assertFalse(target.active or target.complete)
        recover_preserved(target, point_b)
        self.assertIn("workflow-b-only", target.state["workflows"])
        restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(target.state, point_a.state)
        self.assertTrue(target.active and target.complete)

    def test_missing_provider_credentials_are_not_healthy(self):
        source = new_fixture(self.case, "n8n-source")
        source.state = self.n8n_state("A")
        source.external_state = {"provider-credential": "required-external-input"}
        point = capture(self.case, source, "A", exclude_external=("provider-credential",))
        target = new_fixture(self.case, "n8n-new", "EMPTY")
        target.active = False
        with self.assertRaisesRegex(ProtocolFailure, "required external data"):
            restore_new(self.case, source, target, point)
        self.assertFalse(target.active or target.complete)

    def test_shared_negative_matrix_rejects_boundary_failures(self):
        self.assertEqual(
            set(run_negative_cases(self.case)),
            {
                "missing-key",
                "incompatible-version",
                "corrupt-artifact",
                "incomplete-artifact",
                "excluded-external-data",
                "wrong-target",
                "storage-boundary",
                "identity-boundary",
                "shared-guest-scope",
                "partial-recovery",
            },
        )


if __name__ == "__main__":
    unittest.main()
