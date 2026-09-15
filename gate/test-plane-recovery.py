#!/usr/bin/env python3
"""Plane-specific assertions layered on the shared recovery acceptance fixture."""
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
DEFAULTS = ROOT / "ansible/vars/app-defaults/plane.yml"
ROLE_MAIN = ROOT / "ansible/roles/plane/tasks/main.yml"
BACKUP = ROOT / "ansible/roles/plane/tasks/backup.yml"
RESTORE = ROOT / "ansible/roles/plane/tasks/restore.yml"
RECOVERY_HELPER = ROOT / "ansible/roles/plane/files/plane-recovery"
RECOVERY_CONFIG = ROOT / "ansible/roles/plane/templates/plane-recovery.env.j2"
PLAYBOOK = ROOT / "ansible/playbooks/apps/plane.yml"
RESTORE_DISPATCH = ROOT / "ansible/playbooks/maintenance/restore-app.yml"
DEPLOY_JOB = ROOT / "rundeck/jobs/deploy-plane.yaml"
SPEC = ROOT / "docs/specs/plane-recovery.md"


class PlaneRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))["plane_defaults"]
        cls.role_main = ROLE_MAIN.read_text(encoding="utf-8")
        cls.backup = BACKUP.read_text(encoding="utf-8")
        cls.restore = RESTORE.read_text(encoding="utf-8")
        cls.recovery_helper = RECOVERY_HELPER.read_text(encoding="utf-8")
        cls.recovery_config = RECOVERY_CONFIG.read_text(encoding="utf-8")
        cls.playbook = PLAYBOOK.read_text(encoding="utf-8")
        cls.restore_dispatch = RESTORE_DISPATCH.read_text(encoding="utf-8")
        cls.deploy_job = DEPLOY_JOB.read_text(encoding="utf-8")
        cls.spec = SPEC.read_text(encoding="utf-8")
        cls.case = MethodCase(
            product="plane",
            method="native",
            kind="application",
            version="v1.4.2",
            artifact_prefix="fixture/native/plane",
            assertions=("project-record", "server-worker-state", "object-storage", "database-and-redis"),
            source_file="ansible/playbooks/maintenance/restore-app.yml",
        )

    @staticmethod
    def plane_state(marker: str, *, b_only: bool = False) -> dict[str, object]:
        projects = {
            "project-primary": {
                "name": "Fixture project",
                "issue": f"{marker}-issue",
            }
        }
        server_worker = {
            "api-media": f"api-{marker}-asset",
            "worker-media": f"worker-{marker}-asset",
            "rabbitmq": {"queue": f"queue-{marker}"},
        }
        object_storage = {"plane/attachments/document.txt": f"object-{marker}"}
        database_and_redis = {
            "postgresql": {"name": "plane", "marker": marker},
            "redis": {"queue-marker": marker},
        }
        if b_only:
            projects["project-b-only"] = {"name": "B-only project", "issue": "B-issue"}
            server_worker["worker-b-only"] = "B-only-worker-asset"
            object_storage["plane/attachments/b-only.txt"] = "B-only-object"
            database_and_redis["redis"]["b-only-marker"] = "B"
        return {
            "projects": projects,
            "server_worker": server_worker,
            "object_storage": object_storage,
            "database_and_redis": database_and_redis,
            "keys": "vault-only-plane-keys",
        }

    def test_declares_native_four_member_unit_and_shared_guest_fallback(self):
        self.assertEqual(self.defaults["recovery"]["methods"], ["native"])
        self.assertEqual(
            self.defaults["recovery"]["native"],
            {"backup_playbook": "backup-app.yml", "restore_playbook": "restore-app.yml"},
        )
        self.assertEqual(self.defaults["app"]["release"], "v1.4.2")
        self.assertEqual(self.defaults["app"]["database"]["instance"], "postgresql-plane")
        self.assertEqual(self.defaults["app"]["redis"]["instance"], "redis-plane")
        self.assertTrue(self.defaults["backup"]["application_consistent"])
        self.assertEqual(self.defaults["backup"]["postgres_client_image"], "postgres:16-alpine")
        self.assertEqual(self.defaults["backup"]["redis_client_image"], "redis:7-alpine")
        self.assertIn("Schedule the recurring application-consistent backup", self.role_main)
        self.assertIn("backup.schedule.split()", self.role_main)
        self.assertIn("plane_recovery_script", self.backup)
        self.assertIn("PLANE_INSTANCE={{ instance", self.role_main)
        self.assertIn("PLANE_RELEASE={{ app_config.app.release", self.recovery_config)
        for marker in ("pg_dump", "redis-cli", "--rdb", "redis-check-rdb", "pg_restore", "proxmox-backup-client backup"):
            self.assertIn(marker, self.recovery_helper)
        self.assertNotIn("proxmox-backup-client prune", self.recovery_helper)
        self.assertIn("overlapping backup/restore work", self.recovery_helper)
        self.assertIn("Acquire the target recovery lock", self.restore)
        self.assertIn("vault_item_secret_fields:", self.role_main)
        self.assertIn("database_password", self.role_main)
        self.assertIn("object_storage_access_key", self.role_main)
        self.assertIn("object_storage_secret_key", self.role_main)
        self.assertIn("../../../tasks/backup/resolve-pbs-target.yml", self.role_main)
        for member in ("database.pxar", "redis.pxar", "data.pxar", "object-storage.pxar"):
            self.assertIn(member, self.recovery_helper)
            self.assertIn(member, self.restore)
        for marker in ("pg_dump", "redis-cli", "--rdb", "redis-check-rdb", "pg_restore"):
            self.assertIn(marker, self.recovery_helper + self.restore)
        self.assertIn("PLANE_OBJECT_STORAGE_PATH", self.recovery_helper)
        self.assertIn("plane_redis_restore_target", self.restore)
        self.assertIn("restore_source_plane_secrets", self.restore)
        self.assertIn("restore_source_redis", self.restore)
        self.assertIn("target-owned persistent paths", self.restore)
        self.assertIn("plane-release.txt", self.restore)
        self.assertIn("different declared release", self.restore)
        self.assertIn("restore_source_redis.host == _plane_restore_redis_backend.host", self.restore)
        self.assertIn("recovery_isolated", self.playbook)
        self.assertIn("Skip external wiring while the restore target is isolated", self.playbook)
        self.assertIn("plane_restore_target_config", self.restore_dispatch)
        self.assertIn("restore_source_plane_secrets", self.restore_dispatch)
        self.assertIn("restore_source_redis", self.restore_dispatch)
        self.assertIn("object_storage_path", self.restore_dispatch)
        self.assertIn("source and target owned storage paths", self.restore_dispatch)
        self.assertIn("native recovery unit", self.deploy_job)
        self.assertIn("project_managed", self.spec)
        self.assertIn("pbs_guest", self.spec)
        self.assertIn("rebuild-only", self.spec)
        self.assertIn("host/<backup_id>", self.spec)
        self.assertEqual(self.case.version, "v1.4.2")

    def test_new_restore_recovers_all_plane_state_without_source(self):
        source = new_fixture(self.case, "plane-source")
        source.state = self.plane_state("A")
        source.external_state = {
            "postgresql": "named-postgresql-plane",
            "redis": "named-redis-plane",
            "minio": "named-plane-minio",
            "credentials": "vaultwarden-only",
        }
        point = capture(self.case, source, "A")
        source_state_before = deepcopy(source.state)
        source.active = False
        source.reachable = False

        target = new_fixture(self.case, "plane-new", "EMPTY")
        target.active = False
        result = restore_new(self.case, source, target, point)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point.state)
        self.assertEqual(target.state["projects"]["project-primary"]["issue"], "A-issue")
        self.assertEqual(target.state["server_worker"]["rabbitmq"]["queue"], "queue-A")
        self.assertEqual(target.state["object_storage"]["plane/attachments/document.txt"], "object-A")
        self.assertEqual(target.state["database_and_redis"]["redis"]["queue-marker"], "A")
        self.assertEqual(target.connections["route"], target.route)
        self.assertEqual(target.identity, "plane-new")
        self.assertTrue(target.active and target.complete)
        self.assertFalse(source.reachable)
        self.assertEqual(source.state, source_state_before)

    def test_existing_restore_is_a_to_b_to_a_and_retry_uses_retained_b(self):
        target = new_fixture(self.case, "plane-existing", "A")
        target.state = self.plane_state("A")
        point_a = capture(self.case, target, "A")
        target.state = self.plane_state("B", b_only=True)
        point_b = capture(self.case, target, "B")

        result = restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point_a.state)
        self.assertNotIn("project-b-only", target.state["projects"])
        self.assertNotIn("worker-b-only", target.state["server_worker"])
        self.assertNotIn("plane/attachments/b-only.txt", target.state["object_storage"])
        self.assertNotIn("b-only-marker", target.state["database_and_redis"]["redis"])
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
        self.assertIn("project-b-only", target.state["projects"])
        self.assertIn("plane/attachments/b-only.txt", target.state["object_storage"])
        restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(target.state, point_a.state)
        self.assertTrue(target.active and target.complete)

    def test_missing_key_or_external_dependency_is_not_healthy(self):
        source = new_fixture(self.case, "plane-source")
        source.state = self.plane_state("A")
        source.external_state = {
            "postgresql": "named-postgresql-plane",
            "redis": "named-redis-plane",
            "minio": "named-plane-minio",
            "credentials": "vaultwarden-only",
        }
        target = new_fixture(self.case, "plane-new", "EMPTY")
        target.active = False
        point_without_external = capture(
            self.case,
            source,
            "A",
            exclude_external=("credentials",),
        )
        with self.assertRaisesRegex(ProtocolFailure, "required external data"):
            restore_new(self.case, source, target, point_without_external)
        self.assertFalse(target.active or target.complete)

        point_without_key = capture(self.case, source, "A")
        point_without_key = point_without_key.__class__(
            **{**point_without_key.__dict__, "key_available": False}
        )
        with self.assertRaisesRegex(ProtocolFailure, "missing recovery key"):
            restore_new(self.case, source, target, point_without_key)
        self.assertFalse(target.active or target.complete)

    def test_shared_negative_matrix_rejects_boundary_failures(self):
        self.assertEqual(
            set(run_negative_cases(self.case)),
            {
                "missing-key",
                "incompatible-version",
                "corrupt-artifact",
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
