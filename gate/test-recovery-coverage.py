#!/usr/bin/env python3
"""Synthetic tests for the product recovery coverage inventory."""

import importlib.util
from copy import deepcopy
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "recovery_coverage", ROOT / "ansible/files/recovery/coverage.py"
)
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


class CoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = coverage.build_report()
        cls.products = {row["product"]: row for row in cls.report["products"]}

    def test_every_catalog_product_has_recovery_owner_and_priority_is_separate(self):
        catalog = yaml.safe_load((ROOT / "catalog/applications.yml").read_text())
        self.assertEqual(set(self.products), set(catalog["applications"]))
        self.assertEqual(len(self.products), 53)
        for row in self.products.values():
            self.assertGreater(row["recovery_issue"]["number"], 0)
            self.assertEqual(row["priority"]["selection"], "pending_user_selection")
            self.assertIn("pbs_guest", row["available_methods"])
            self.assertTrue(row["methods"]["pbs_guest"]["recovery_unit"])

    def test_shared_guest_and_external_dependencies_are_visible(self):
        services = self.products["n8n"]
        self.assertTrue(services["shared_guest_effects"][0].startswith("shared stack guest services"))
        self.assertEqual(services["methods"]["pbs_guest"]["availability"], "applicable")
        self.assertEqual(services["credentials"]["status"], "unknown")
        self.assertEqual(services["external_data"][0]["kind"], "database")
        self.assertEqual(services["external_data"][0]["status"], "unknown")

        k8s = self.products["mixpost"]
        self.assertEqual(k8s["methods"]["pbs_guest"]["availability"], "shared_guest_only")
        self.assertIn("not an application-only restore", k8s["methods"]["pbs_guest"]["recovery_unit"])
        self.assertEqual(k8s["methods"]["native"]["evidence"]["schedule"]["status"], "configured")
        self.assertEqual(k8s["methods"]["native"]["evidence"]["artifact"]["status"], "unknown")

    def test_rebuild_only_is_explicit_and_undeclared_method_is_not_healthy(self):
        self.assertEqual(self.products["homepage"]["fallback"]["disposition"], "rebuild-only")
        self.assertEqual(self.products["caddy"]["methods"]["native"]["availability"], "not_declared")
        self.assertEqual(self.products["caddy"]["methods"]["native"]["evidence"]["artifact"]["status"], "unknown")
        self.assertEqual(self.products["caddy"]["fallback"]["disposition"], "pbs-guest-only")

    def test_rebuild_only_cannot_conflict_with_declared_recovery_method(self):
        resolved, _, _ = coverage.load_inputs()
        for method in ("native", "project_managed"):
            item = deepcopy(resolved["homepage"])
            item["manifest"]["rebuild_only"] = True
            item["defaults"]["recovery"] = {"methods": [method]}
            with self.assertRaisesRegex(ValueError, "rebuild_only"):
                coverage._product(
                    "homepage",
                    item,
                    resolved,
                    audit=None,
                    native_evidence=None,
                )

    def test_explicit_guest_mapping_reuses_pr81_evidence_without_inference(self):
        audit = {
            "schema_version": 1,
            "guests": [{
                "name": "legacy-sonarr",
                "vmid": 123,
                "schedule": "enabled",
                "guest_snapshot": "fresh",
                "artifact_id": "pbs:backup/ct/123/point",
                "captured_at": "2026-09-05T00:00:00Z",
                "latest_candidate": {"age_hours": 3},
                "artifact_integrity": "unverified",
                "restore_test": "unverified",
                "external_or_excluded_data": [{"device": "mp0", "reason": "bind-or-device-mount"}],
                "evidence": {"state": "verified_fresh"},
            }],
            "product_guests": {"sonarr": ["legacy-sonarr"]},
        }
        report = coverage.build_report(audit=audit)
        sonarr = next(row for row in report["products"] if row["product"] == "sonarr")
        evidence = sonarr["methods"]["pbs_guest"]["evidence"]
        self.assertEqual(evidence["artifact"]["status"], "verified")
        self.assertEqual(evidence["artifact"]["artifact_id"], "pbs:backup/ct/123/point")
        self.assertEqual(evidence["integrity"]["status"], "unknown")
        self.assertEqual(evidence["external_data"]["status"], "excluded")

        # Without an explicit source-to-product mapping, a fresh guest remains unknown.
        no_mapping_audit = dict(audit)
        no_mapping_audit.pop("product_guests")
        no_mapping = coverage.build_report(audit=no_mapping_audit)
        sonarr = next(row for row in no_mapping["products"] if row["product"] == "sonarr")
        self.assertEqual(sonarr["methods"]["pbs_guest"]["evidence"]["artifact"]["status"], "unknown")


    def test_explicit_native_evidence_updates_artifact_external_and_credential_states(self):
        report = coverage.build_report(native_evidence={
            "litellm": {
                "artifact": {"state": "stale", "artifact_id": "host/litellm/old"},
                "external_data": {"status": "verified"},
                "credentials": {"status": "failed", "notes": "fixture credential check failed"},
            },
        })
        litellm = next(row for row in report["products"] if row["product"] == "litellm")
        self.assertEqual(litellm["methods"]["native"]["evidence"]["artifact"]["status"], "stale")
        self.assertEqual(litellm["external_data"][0]["status"], "verified")
        self.assertEqual(litellm["credentials"]["status"], "failed")

    def test_unreadable_guest_evidence_is_not_promoted(self):
        audit = {
            "guests": [{
                "name": "legacy-sonarr",
                "vmid": 123,
                "schedule": "unknown",
                "guest_snapshot": "unknown",
                "evidence": {"state": "configured_unverified"},
            }],
            "product_guests": {"sonarr": ["legacy-sonarr"]},
        }
        report = coverage.build_report(audit=audit)
        sonarr = next(row for row in report["products"] if row["product"] == "sonarr")
        evidence = sonarr["methods"]["pbs_guest"]["evidence"]
        self.assertEqual(evidence["artifact"]["status"], "unknown")
        self.assertEqual(evidence["artifact"]["reason"], "guest audit evidence was unreadable")
        self.assertEqual(evidence["schedule"]["status"], "unknown")

    def test_status_fixture_preserves_negative_evidence_states(self):
        fixture = yaml.safe_load((ROOT / "gate/fixtures/recovery-coverage/status.yml").read_text())
        expected = {
            "stale-native-artifact": "stale",
            "missing-native-artifact": "missing",
            "excluded-external-volume": "excluded",
            "incomplete-artifact": "incomplete",
            "unreadable-inventory": "unknown",
        }
        for item in fixture["evidence"]:
            reduced = coverage._component(item["artifact"])
            if item["id"] == "excluded-external-volume":
                self.assertEqual(coverage._component(item["external_data"])["status"], "excluded")
            else:
                self.assertEqual(reduced["status"], expected[item["id"]])
        self.assertEqual(coverage._component({"state": "verified_fresh"})["status"], "verified")

    def test_legacy_source_is_explicit_but_not_selected(self):
        self.assertEqual(len(self.report["legacy_sources"]), 1)
        legacy = self.report["legacy_sources"][0]
        self.assertEqual(legacy["product"], "sonarr")
        self.assertEqual(legacy["priority"], "pending_user_selection")
        self.assertIn("do not adopt", legacy["disposition"])


if __name__ == "__main__":
    unittest.main()
