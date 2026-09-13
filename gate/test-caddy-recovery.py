#!/usr/bin/env python3
"""Caddy-specific assertions layered on the shared PBS guest fixture."""
from __future__ import annotations

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
DEFAULTS = ROOT / "ansible/vars/app-defaults/caddy.yml"
ROLE = ROOT / "ansible/roles/caddy/tasks/main.yml"
WIRING = ROOT / "ansible/tasks/wiring/caddy.yml"
DEPLOY_JOB = ROOT / "rundeck/jobs/deploy-caddy.yaml"
SPEC = ROOT / "docs/specs/caddy-recovery.md"


class CaddyRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))["caddy_defaults"]
        cls.role = ROLE.read_text(encoding="utf-8")
        cls.wiring = WIRING.read_text(encoding="utf-8")
        cls.deploy_job = DEPLOY_JOB.read_text(encoding="utf-8")
        cls.spec = SPEC.read_text(encoding="utf-8")
        cls.case = MethodCase(
            product="caddy",
            method="pbs_guest",
            kind="lxc",
            version="installed-caddy",
            artifact_prefix="backup/ct/caddy",
            assertions=("route-table", "certificate-state", "access-policy", "upstream-connection"),
            source_file="ansible/playbooks/maintenance/restore-guest.yml",
            affected_scope=("caddy-edge", "routed-workloads"),
        )

    @staticmethod
    def caddy_state(marker: str, *, b_only: bool = False) -> dict[str, object]:
        routes = {
            "route_radarr": {
                "upstream": "radarr-4k:7878",
                "access": "internal",
                "tls": "estate-wildcard",
            }
        }
        if b_only:
            routes["route_caddy-b-only"] = {
                "upstream": "fixture-b:9443",
                "access": "authenticated",
                "tls": "estate-wildcard",
            }
        return {
            "marker": marker,
            "routes": routes,
            "certificate": {"estate-wildcard": "private-key-and-ocsp"},
            "access_policy": {"route_radarr": ["192.0.2.0/24"]},
        }

    def test_role_and_operator_surface_preserve_the_caddy_recovery_boundary(self):
        proxmox = self.defaults["proxmox"]
        self.assertEqual(proxmox["type"], "lxc")
        self.assertIn("_.shared", proxmox["tags"])
        self.assertNotIn("recovery", self.defaults)
        self.assertIn("caddy_autosave_file", self.role)
        self.assertIn("--resume", self.role)
        self.assertIn("/var/lib/caddy/.local/share/caddy", self.spec)
        self.assertIn("remote_ip", self.wiring)
        self.assertIn("forward_auth", self.wiring)
        self.assertIn("Backup Guest", self.deploy_job)
        self.assertIn("Restore Guest", self.deploy_job)
        self.assertIn("no Caddy-specific Backup/Restore action", self.deploy_job)

    def test_new_restore_recovers_routes_certificates_policy_and_target_connection(self):
        source = new_fixture(self.case, "caddy-source")
        source.state = self.caddy_state("A")
        source.external_state = {"dns-provider-credential": "required-external-input"}
        point = capture(self.case, source, "A")
        source_state_before_restore = source.state.copy()
        source.active = False
        source.reachable = False

        target = new_fixture(self.case, "caddy-new", "EMPTY")
        target.active = False
        result = restore_new(
            self.case,
            source,
            target,
            point,
            required_scope=self.case.affected_scope,
        )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point.state)
        self.assertEqual(target.state["routes"]["route_radarr"]["upstream"], "radarr-4k:7878")
        self.assertEqual(target.state["certificate"]["estate-wildcard"], "private-key-and-ocsp")
        self.assertEqual(target.state["access_policy"]["route_radarr"], ["192.0.2.0/24"])
        self.assertEqual(target.connections["route"], target.route)
        self.assertEqual(target.identity, "caddy-new")
        self.assertTrue(target.complete and target.active)
        self.assertFalse(source.reachable)
        self.assertEqual(source.state, source_state_before_restore)

    def test_existing_restore_removes_b_only_state_and_retry_uses_preserved_point(self):
        target = new_fixture(self.case, "caddy-existing", "A")
        target.state = self.caddy_state("A")
        point_a = capture(self.case, target, "A")
        target.state = self.caddy_state("B", b_only=True)
        point_b = capture(self.case, target, "B")

        result = restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point_a.state)
        self.assertNotIn("route_caddy-b-only", target.state["routes"])
        self.assertEqual(point_b.state["marker"], "B")
        self.assertNotEqual(point_a.artifact_id, point_b.artifact_id)

        target.state = dict(point_b.state)
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
        self.assertIn("route_caddy-b-only", target.state["routes"])
        restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(target.state, point_a.state)
        self.assertTrue(target.active and target.complete)

    def test_shared_negative_matrix_rejects_caddy_boundary_failures(self):
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

    def test_missing_dns_material_is_not_reported_as_a_successful_restore(self):
        source = new_fixture(self.case, "caddy-source")
        source.state = self.caddy_state("A")
        source.external_state = {"dns-provider-credential": "required-external-input"}
        point = capture(self.case, source, "A", exclude_external=("dns-provider-credential",))
        target = new_fixture(self.case, "caddy-new", "EMPTY")
        target.active = False
        with self.assertRaisesRegex(ProtocolFailure, "required external data"):
            restore_new(self.case, source, target, point, required_scope=self.case.affected_scope)
        self.assertFalse(target.active or target.complete)


if __name__ == "__main__":
    unittest.main()
