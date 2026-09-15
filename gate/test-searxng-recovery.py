#!/usr/bin/env python3
"""SearXNG recovery assertions for the shared fallback and rebuild-only path."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import unittest

import yaml

from recovery_acceptance import (
    MethodCase,
    ProtocolFailure,
    run_protocol,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "ansible/vars/app-defaults/searxng.yml"
MANIFEST = ROOT / "catalog/recovery.yml"
ROLE = ROOT / "ansible/roles/searxng/tasks/main.yml"
WORKLOAD = ROOT / "ansible/roles/searxng/templates/manifest.yaml.j2"
PLAYBOOK = ROOT / "ansible/playbooks/apps/searxng.yml"
DEPLOY_JOB = ROOT / "rundeck/jobs/deploy-searxng.yaml"
SPEC = ROOT / "docs/specs/searxng-recovery.md"


@dataclass
class RebuildFixture:
    name: str
    route: str
    configuration: dict[str, object]
    limiter_cache: dict[str, object] = field(default_factory=dict)
    identity: str = ""
    active: bool = True
    complete: bool = False
    isolated: bool = True
    reachable: bool = True

    def __post_init__(self):
        if not self.identity:
            self.identity = self.name


def rebuild_new(
    source: RebuildFixture,
    target: RebuildFixture,
    configuration: dict[str, object],
    *,
    failure_stage: str | None = None,
) -> None:
    if target.name == source.name:
        raise ProtocolFailure("new rebuild target aliases the source")
    if not target.isolated:
        raise ProtocolFailure("new rebuild target is not isolated")
    if target.route == source.route:
        raise ProtocolFailure("new rebuild target takes the source route")
    target.active = False
    target.complete = False
    if failure_stage == "before_replace":
        raise ProtocolFailure("injected rebuild interruption before replacement")
    target.configuration = deepcopy(configuration)
    # Redis only enforces limiter behavior. A rebuild intentionally starts with no counters.
    target.limiter_cache = {}
    target.identity = target.name
    if failure_stage == "after_replace":
        raise ProtocolFailure("injected rebuild interruption after replacement")
    target.active = True
    target.complete = True


def rebuild_existing(
    target: RebuildFixture,
    configuration: dict[str, object],
    preserved_b: dict[str, object],
    *,
    failure_stage: str | None = None,
) -> None:
    if preserved_b is target:
        raise ProtocolFailure("existing rebuild has no independent B point")
    if preserved_b["revision"] == configuration["revision"]:
        raise ProtocolFailure("existing rebuild has no distinguishable B point")
    target.active = False
    target.complete = False
    if failure_stage == "after_quiesce":
        raise ProtocolFailure("injected rebuild interruption after quiesce")
    target.configuration = deepcopy(configuration)
    target.limiter_cache = {}
    if failure_stage == "after_replace":
        raise ProtocolFailure("injected rebuild interruption after replacement")
    target.active = True
    target.complete = True


def recover_preserved_b(target: RebuildFixture, preserved_b: dict[str, object]) -> None:
    target.configuration = deepcopy(preserved_b["configuration"])
    target.limiter_cache = {}
    target.active = True
    target.complete = True


class SearXNGRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))["searxng_defaults"]
        cls.manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
        cls.role = ROLE.read_text(encoding="utf-8")
        cls.workload = WORKLOAD.read_text(encoding="utf-8")
        cls.playbook = PLAYBOOK.read_text(encoding="utf-8")
        cls.deploy_job = DEPLOY_JOB.read_text(encoding="utf-8")
        cls.spec = SPEC.read_text(encoding="utf-8")
        cls.case = MethodCase(
            product="searxng",
            method="pbs_guest",
            kind="vm",
            version="native-k3s-pbs-artifact",
            artifact_prefix="backup/vm/searxng-cluster",
            assertions=("rendered-config", "disposable-limiter-cache", "target-route", "cluster-connection"),
            source_file="ansible/playbooks/maintenance/restore-guest.yml",
            issue_reference=187,
            affected_scope=("k3s-cluster", "searxng", "shared-kubernetes-workloads"),
        )

    def test_declares_rebuild_only_boundary_and_isolated_operator_path(self):
        self.assertEqual(self.defaults["hosting"], "kubernetes")
        self.assertEqual(self.defaults["app"]["image"], "searxng/searxng:latest")
        self.assertEqual(self.defaults["app"]["limiter"]["image"], "redis:7-alpine")
        self.assertNotIn("recovery", self.defaults)
        self.assertNotIn("backup", self.defaults)
        searxng = self.manifest["products"]["searxng"]
        self.assertTrue(searxng["rebuild_only"])
        self.assertNotIn("kind: PersistentVolumeClaim", self.workload)
        self.assertNotIn("persistentVolumeClaim", self.workload)
        self.assertIn('vault_item_name: "homelab-infra/apps/{{ instance }}"', self.role)
        self.assertIn("redis-password", self.role)
        self.assertIn("settings.yml", self.workload)
        self.assertIn("recovery_isolated", self.playbook)
        self.assertIn("Skip external wiring while the rebuild target is isolated", self.playbook)
        self.assertIn("recovery_isolated", self.deploy_job)
        self.assertIn("rebuild-only", self.spec)
        self.assertIn("shared whole-guest fallback", self.spec)

    def test_shared_pbs_fallback_runs_both_destinations_and_negative_matrix(self):
        result = run_protocol(self.case)
        self.assertEqual(result["new"]["status"], "passed")
        self.assertEqual(result["existing"]["status"], "passed")
        self.assertTrue(result["new"]["source_isolated"])
        self.assertTrue(result["existing"]["b_point_retained"])
        self.assertEqual(
            set(result["negative_cases"]),
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
        self.assertEqual(result["evidence"]["issue_reference"], 187)
        self.assertFalse(result["evidence"]["live_verified"])

    def test_new_rebuild_is_source_independent_and_does_not_need_cache_state(self):
        source = RebuildFixture(
            "searxng-source",
            "searxng.internal.invalid",
            {"revision": "A", "formats": ["html", "json"], "secret_key": "vault-ref"},
            {"counter": "source-only"},
        )
        source_state = deepcopy(source.configuration)
        rebuild_configuration = deepcopy(source.configuration)
        source.reachable = False
        target = RebuildFixture(
            "searxng-new",
            "searxng-new.internal.invalid",
            {"revision": "EMPTY"},
            {"counter": "stale-target-cache"},
            active=False,
        )
        rebuild_new(source, target, rebuild_configuration)
        self.assertEqual(target.configuration, source.configuration)
        self.assertEqual(target.limiter_cache, {})
        self.assertEqual(target.identity, "searxng-new")
        self.assertTrue(target.active and target.complete and target.isolated)
        self.assertFalse(source.reachable)
        self.assertEqual(source.configuration, source_state)

    def test_existing_rebuild_is_a_to_b_to_a_and_retry_uses_independent_b_point(self):
        target = RebuildFixture(
            "searxng-existing",
            "searxng-existing.internal.invalid",
            {"revision": "A", "formats": ["html", "json"]},
            {"counter": "A"},
        )
        target.configuration = {"revision": "B", "formats": ["html"], "b_only": "fixture"}
        target.limiter_cache = {"counter": "B"}
        preserved_b = {
            "revision": target.configuration["revision"],
            "configuration": deepcopy(target.configuration),
        }
        restore_a = {"revision": "A", "formats": ["html", "json"]}
        rebuild_existing(target, restore_a, preserved_b)
        self.assertEqual(target.configuration, restore_a)
        self.assertNotIn("b_only", target.configuration)
        self.assertEqual(target.limiter_cache, {})
        self.assertEqual(preserved_b["configuration"]["revision"], "B")

        target.configuration = deepcopy(preserved_b["configuration"])
        target.limiter_cache = {"counter": "B"}
        target.active = True
        target.complete = True
        with self.assertRaisesRegex(ProtocolFailure, "after replacement"):
            rebuild_existing(target, restore_a, preserved_b, failure_stage="after_replace")
        self.assertFalse(target.active or target.complete)
        recover_preserved_b(target, preserved_b)
        self.assertEqual(target.configuration["revision"], "B")
        self.assertEqual(target.limiter_cache, {})
        rebuild_existing(target, restore_a, preserved_b)
        self.assertEqual(target.configuration, restore_a)
        self.assertTrue(target.active and target.complete)

    def test_rebuild_failure_before_replacement_leaves_target_unchanged(self):
        source = RebuildFixture(
            "searxng-source",
            "source.invalid",
            {"revision": "A"},
        )
        target = RebuildFixture(
            "searxng-new",
            "target.invalid",
            {"revision": "EMPTY"},
            active=False,
        )
        before = deepcopy(target)
        with self.assertRaisesRegex(ProtocolFailure, "before replacement"):
            rebuild_new(source, target, source.configuration, failure_stage="before_replace")
        self.assertEqual(target, before)


if __name__ == "__main__":
    unittest.main()
