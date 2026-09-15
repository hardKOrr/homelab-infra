#!/usr/bin/env python3
"""Odoo-specific assertions layered on the shared recovery acceptance fixture."""
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
DEFAULTS = ROOT / "ansible/vars/app-defaults/odoo.yml"
BACKUP = ROOT / "ansible/roles/odoo/tasks/backup.yml"
RESTORE = ROOT / "ansible/roles/odoo/tasks/restore.yml"
PLAYBOOK = ROOT / "ansible/playbooks/apps/odoo.yml"
RESTORE_DISPATCH = ROOT / "ansible/playbooks/maintenance/restore-app.yml"
DEPLOY_JOB = ROOT / "rundeck/jobs/deploy-odoo.yaml"
SPEC = ROOT / "docs/specs/odoo-recovery.md"


class OdooRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))["odoo_defaults"]
        cls.backup = BACKUP.read_text(encoding="utf-8")
        cls.restore = RESTORE.read_text(encoding="utf-8")
        cls.playbook = PLAYBOOK.read_text(encoding="utf-8")
        cls.restore_dispatch = RESTORE_DISPATCH.read_text(encoding="utf-8")
        cls.deploy_job = DEPLOY_JOB.read_text(encoding="utf-8")
        cls.spec = SPEC.read_text(encoding="utf-8")
        cls.case = MethodCase(
            product="odoo",
            method="native",
            kind="application",
            version="18.0",
            artifact_prefix="fixture/native/odoo",
            assertions=("crm-record", "filestore-document", "database-connection", "admin-login"),
            source_file="ansible/playbooks/maintenance/restore-app.yml",
        )

    @staticmethod
    def odoo_state(marker: str, *, b_only: bool = False) -> dict[str, object]:
        leads = {
            "lead-primary": {
                "name": "Fixture opportunity",
                "stage": "qualified",
                "marker": marker,
            }
        }
        attachments = {"quote.pdf": f"binary-{marker}"}
        if b_only:
            leads["lead-b-only"] = {
                "name": "B-only opportunity",
                "stage": "new",
                "marker": "B",
            }
            attachments["b-only.txt"] = "B-only attachment"
        return {
            "crm": {"leads": leads},
            "filestore": {"attachments": attachments},
            "users": {"admin": "source-user-hash"},
        }

    def test_declares_the_native_paired_state_and_shared_fallback(self):
        self.assertEqual(self.defaults["recovery"]["methods"], ["native"])
        self.assertEqual(
            self.defaults["recovery"]["native"],
            {"backup_playbook": "backup-app.yml", "restore_playbook": "restore-app.yml"},
        )
        self.assertTrue(self.defaults["backup"]["application_consistent"])
        self.assertEqual(self.defaults["backup"]["postgres_client_image"], "postgres:16-alpine")
        self.assertIn("paired snapshot", self.deploy_job.lower())
        self.assertIn("../../../tasks/backup/resolve-pbs-target.yml", self.backup)
        self.assertIn("../../../tasks/backup/resolve-pbs-target.yml", self.restore)
        self.assertIn("database.pxar", self.backup)
        self.assertIn("filestore.pxar", self.backup)
        self.assertIn("pg_dump", self.backup)
        self.assertIn("pg_restore", self.restore)
        self.assertIn("app_config.app.filestore_path", self.backup)
        self.assertIn("app_config.app.database.name", self.restore)
        self.assertIn("left stopped", self.restore.lower())
        self.assertIn("recovery_isolated", self.playbook)
        self.assertIn("Skip external wiring while the restore target is isolated", self.playbook)
        self.assertIn("_ra_app == 'odoo'", self.restore_dispatch)
        self.assertIn("'filestore_path'", self.restore_dispatch)
        self.assertIn("replace('{{ instance }}', hostvars['localhost']._ra_target)", self.restore_dispatch)
        self.assertIn("project_managed", self.spec)
        self.assertIn("pbs_guest", self.spec)
        self.assertIn("host/<backup_id>", self.spec)

    def test_new_restore_recovers_crm_filestore_and_target_connections_without_source(self):
        source = new_fixture(self.case, "odoo-source")
        source.state = self.odoo_state("A")
        source.external_state = {"external-library": "postgresql-odoo-and-vault"}
        point = capture(self.case, source, "A")
        source_state_before = deepcopy(source.state)
        source.active = False
        source.reachable = False

        target = new_fixture(self.case, "odoo-new", "EMPTY")
        target.active = False
        result = restore_new(self.case, source, target, point)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point.state)
        self.assertEqual(target.state["crm"]["leads"]["lead-primary"]["marker"], "A")
        self.assertEqual(target.state["filestore"]["attachments"]["quote.pdf"], "binary-A")
        self.assertEqual(target.connections["route"], target.route)
        self.assertEqual(target.identity, "odoo-new")
        self.assertTrue(target.active and target.complete)
        self.assertFalse(source.reachable)
        self.assertEqual(source.state, source_state_before)

    def test_existing_restore_is_a_to_b_to_a_and_retry_uses_retained_b(self):
        target = new_fixture(self.case, "odoo-existing", "A")
        target.state = self.odoo_state("A")
        point_a = capture(self.case, target, "A")
        target.state = self.odoo_state("B", b_only=True)
        point_b = capture(self.case, target, "B")

        result = restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point_a.state)
        self.assertNotIn("lead-b-only", target.state["crm"]["leads"])
        self.assertNotIn("b-only.txt", target.state["filestore"]["attachments"])
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
        self.assertIn("lead-b-only", target.state["crm"]["leads"])
        restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(target.state, point_a.state)
        self.assertTrue(target.active and target.complete)

    def test_missing_database_or_credential_material_is_not_healthy(self):
        source = new_fixture(self.case, "odoo-source")
        source.state = self.odoo_state("A")
        point = capture(self.case, source, "A", exclude_external=("external-library",))
        target = new_fixture(self.case, "odoo-new", "EMPTY")
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
