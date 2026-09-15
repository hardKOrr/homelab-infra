#!/usr/bin/env python3
"""Fixture tests for the common recovery acceptance protocol (#80)."""
from __future__ import annotations

import unittest

from recovery_acceptance import (
    assert_public_report,
    artifact_identity,
    build_rollup,
    catalog_remaining,
    load_method_cases,
    run_protocol,
)


class RecoveryAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_method_cases()

    def test_matrix_covers_shared_pbs_vm_and_lxc_and_declared_products(self):
        ids = {case.case_id for case in self.cases}
        self.assertIn("pbs-guest:pbs_guest:vm", ids)
        self.assertIn("pbs-guest:pbs_guest:lxc", ids)
        pbs_cases = [case for case in self.cases if case.method == "pbs_guest"]
        self.assertEqual(len({artifact_identity(case, "A") for case in pbs_cases}), len(pbs_cases))
        for case in pbs_cases:
            identity = artifact_identity(case, "A")
            self.assertIn("fixture/pbs_guest/", identity)
            self.assertIn(f"/{case.product}/", identity)
        plane = next(case for case in self.cases if case.product == "plane" and case.method == "native")
        self.assertEqual(plane.version, "v1.4.2")
        native = {case.product for case in self.cases if case.method == "native"}
        self.assertEqual(
            native,
            {
                "actual-budget",
                "comfyui",
                "hi-events",
                "home-assistant",
                "immich",
                "jellyseerr",
                "litellm",
                "maintainerr",
                "mixpost",
                "ollama",
                "open-webui",
                "odoo",
                "n8n",
                "plane",
            },
        )

    def test_every_case_runs_new_and_existing_protocol(self):
        for case in self.cases:
            with self.subTest(case=case.case_id):
                result = run_protocol(case)
                self.assertEqual(result["new"]["status"], "passed")
                self.assertEqual(result["existing"]["status"], "passed")
                self.assertTrue(result["new"]["source_isolated"])
                self.assertTrue(result["existing"]["b_point_retained"])
                self.assertIn("partial-recovery", result["negative_cases"])

    def test_rollup_separates_implementation_fixture_and_live_evidence(self):
        report = build_rollup(self.cases, fixture_tested=[case.case_id for case in self.cases])
        implemented = {row["case"] for row in report["implemented"]}
        self.assertEqual(implemented, set(report["fixture_tested"]))
        self.assertEqual(report["live_verified"], [])
        self.assertEqual(report["ordering"].split(";")[0], "user-supplied")
        self.assertEqual(report["catalog_remaining"], catalog_remaining(self.cases))
        assert_public_report(report)

    def test_negative_matrix_rejects_partial_and_boundary_failures(self):
        result = run_protocol(self.cases[0])
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


if __name__ == "__main__":
    unittest.main()
