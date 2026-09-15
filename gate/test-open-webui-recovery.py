#!/usr/bin/env python3
"""Open WebUI recovery dispositions, secrets, and two-destination fixture checks."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib.util
from pathlib import Path
import unittest

import yaml

from recovery_acceptance import (
    MethodCase,
    ProtocolFailure,
    assert_public_report,
    capture,
    evidence_record,
    new_fixture,
    recover_preserved,
    restore_existing,
    restore_new,
    run_negative_cases,
    run_protocol,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "ansible/vars/app-defaults/open-webui.yml"
ROLE_MAIN = ROOT / "ansible/roles/open-webui/tasks/main.yml"
BACKUP = ROOT / "ansible/roles/open-webui/tasks/backup.yml"
BACKUP_HELPER = ROOT / "ansible/roles/open-webui/files/open-webui-recovery"
BACKUP_CONFIG = ROOT / "ansible/roles/open-webui/templates/open-webui-recovery.env.j2"
RESTORE = ROOT / "ansible/roles/open-webui/tasks/restore.yml"
RESTORE_DISPATCH = ROOT / "ansible/playbooks/maintenance/restore-app.yml"
APP_PLAYBOOK = ROOT / "ansible/playbooks/apps/open-webui.yml"
SPEC = ROOT / "docs/specs/open-webui-recovery.md"

coverage_spec = importlib.util.spec_from_file_location(
    "recovery_coverage", ROOT / "ansible/files/recovery/coverage.py"
)
coverage = importlib.util.module_from_spec(coverage_spec)
coverage_spec.loader.exec_module(coverage)


class OpenWebUIRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))["open_webui_defaults"]
        cls.main = ROLE_MAIN.read_text(encoding="utf-8")
        cls.backup = BACKUP.read_text(encoding="utf-8")
        cls.backup_helper = BACKUP_HELPER.read_text(encoding="utf-8")
        cls.backup_config = BACKUP_CONFIG.read_text(encoding="utf-8")
        cls.restore = RESTORE.read_text(encoding="utf-8")
        cls.restore_dispatch = RESTORE_DISPATCH.read_text(encoding="utf-8")
        cls.app_playbook = APP_PLAYBOOK.read_text(encoding="utf-8")
        cls.spec = SPEC.read_text(encoding="utf-8")
        cls.case = MethodCase(
            product="open-webui",
            method="native",
            kind="application",
            version="main",
            artifact_prefix="fixture/native/open-webui",
            assertions=(
                "chat-record",
                "upload-file",
                "user-login",
                "generated-key",
                "named-upstream-connections",
            ),
            source_file="ansible/vars/app-defaults/open-webui.yml",
            issue_reference=185,
            affected_scope=("fixture-open-webui", "fixture-named-upstreams"),
        )

    @staticmethod
    def webui_state(marker: str, *, b_only: bool = False) -> dict[str, object]:
        chats = {"chat-primary": f"{marker}-conversation"}
        uploads = {"upload-primary": f"{marker}-document.pdf"}
        if b_only:
            chats["chat-b-only"] = "B-only conversation"
            uploads["upload-b-only"] = "B-only document.pdf"
        return {
            "webui_db": {
                "users": {"owner": "fixture-password-hash"},
                "chats": chats,
                "settings": {"marker": marker},
            },
            "uploads": uploads,
            "vector_db": {"knowledge": f"{marker}-vector"},
            "cache": {"marker": marker},
        }

    def test_declares_native_method_and_exact_tracker_version_label(self):
        self.assertEqual(self.defaults["recovery"]["methods"], ["native"])
        self.assertEqual(
            self.defaults["recovery"]["native"],
            {"backup_playbook": "backup-app.yml", "restore_playbook": "restore-app.yml"},
        )
        self.assertEqual(self.defaults["app"]["image"], "ghcr.io/open-webui/open-webui:main")
        self.assertTrue(self.defaults["backup"]["application_consistent"])
        self.assertEqual(self.defaults["backup"]["schedule"], "0 3 * * *")
        self.assertEqual(self.case.version, "main")
        self.assertEqual(self.case.issue_reference, 185)
        self.assertIn("pbs_guest", self.spec)
        self.assertIn("project_managed", self.spec)
        self.assertIn("rebuild-only", self.spec)
        self.assertIn("mutable", self.spec)

    def test_schedule_and_manual_backup_share_one_nonpruning_helper(self):
        self.assertIn("Schedule application-consistent backup", self.main)
        self.assertIn('cron_file: "homelab-infra-open-webui-{{ instance }}"', self.main)
        self.assertIn("recovery_isolated", self.main)
        self.assertIn("open-webui-recovery", self.main)
        self.assertIn("open_webui_recovery_script", self.backup)
        self.assertIn("--backup", self.backup)
        self.assertIn("data.pxar:/source", self.backup_helper)
        self.assertIn("PRAGMA integrity_check", self.backup_helper)
        self.assertIn('export BACKUP_ID="$OPEN_WEBUI_BACKUP_ID"', self.backup_helper)
        self.assertIn("homelab-infra-open-webui-${INSTANCE}.lock", self.backup_helper)
        self.assertNotIn("proxmox-backup-client prune", self.backup_helper)
        self.assertNotIn("retention_days", self.defaults["backup"])
        for fragment in (
            "PBS_REPOSITORY=",
            "PBS_FINGERPRINT=",
            "PBS_PASSWORD=",
            "OPEN_WEBUI_BACKUP_ID=",
        ):
            self.assertIn(fragment, self.backup_config)
        self.assertIn("mode: \"0600\"", self.main)
        self.assertIn("no_log: true", self.main)

    def test_restore_carries_source_key_and_verifies_a_and_b_before_mutation(self):
        self.assertIn("restore_source_open_webui_secret_key", self.restore_dispatch)
        self.assertIn("_ra_app == 'open-webui'", self.restore_dispatch)
        self.assertIn("restore_target_backup_id", self.restore_dispatch)
        self.assertIn("vault_item_name: \"homelab-infra/apps/{{ restore_target", self.restore)
        self.assertIn("vault_item_secret_fields: [secret_key]", self.restore)
        self.assertIn("WEBUI_SECRET_KEY=", self.restore)
        self.assertIn("restore_source_open_webui_secret_key", self.restore)
        self.assertIn("PRAGMA integrity_check", self.restore)
        self.assertIn("_open_webui_restore_stopped", self.restore)
        self.assertIn("pre_restore_point", self.restore)
        self.assertIn("stopped or isolated", self.restore.lower())
        self.assertIn("recovery_isolated", self.app_playbook)
        self.assertIn("Skip external wiring while the restore target is isolated", self.app_playbook)

        b_verify = self.restore.index("Verify the independent existing-target B point")
        a_verify = self.restore.index("Extract and validate selected A")
        stop = self.restore.index("Stop only Open WebUI after both points verify")
        replace = self.restore.index("Replace the data directory after artifact validation")
        start = self.restore.index("Start only Open WebUI")
        self.assertLess(b_verify, a_verify)
        self.assertLess(a_verify, stop)
        self.assertLess(stop, replace)
        self.assertLess(replace, start)

    def test_new_restore_recovers_app_state_and_key_without_upstream_model_data(self):
        source = new_fixture(self.case, "open-webui-source")
        source.state = self.webui_state("A")
        source.external_state = {
            "generated-key": "fixture-only-key-marker",
            "named-upstreams": "ollama-source,litellm-source",
            "upstream-model-data": "separately-owned-model-store",
        }
        point = capture(self.case, source, "A", exclude_external=("upstream-model-data",))
        before = deepcopy(source.state)
        source.reachable = False
        source.active = False

        target = new_fixture(self.case, "open-webui-new", "EMPTY")
        target.active = False
        result = restore_new(self.case, source, target, point, require_external=False)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point.state)
        self.assertIn("A-conversation", target.state["webui_db"]["chats"]["chat-primary"])
        self.assertEqual(target.state["uploads"]["upload-primary"], "A-document.pdf")
        self.assertEqual(target.external_state["generated-key"], "fixture-only-key-marker")
        self.assertNotIn("upstream-model-data", target.external_state)
        self.assertEqual(target.identity, "open-webui-new")
        self.assertTrue(target.isolated and target.active and target.complete)
        self.assertFalse(source.reachable)
        self.assertEqual(source.state, before)
        public = evidence_record(self.case, point, point, target)
        assert_public_report(public)
        self.assertEqual(public["issue_reference"], 185)
        self.assertEqual(public["live_verified"], False)

    def test_existing_restore_is_a_to_b_to_a_and_retry_uses_retained_b(self):
        target = new_fixture(self.case, "open-webui-existing")
        target.state = self.webui_state("A")
        point_a = capture(self.case, target, "A")
        target.state = self.webui_state("B", b_only=True)
        point_b = capture(self.case, target, "B")

        result = restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(target.state, point_a.state)
        self.assertNotIn("chat-b-only", target.state["webui_db"]["chats"])
        self.assertNotIn("upload-b-only", target.state["uploads"])
        self.assertNotEqual(point_a.artifact_id, point_b.artifact_id)

        # Bad B evidence must be rejected before target mutation, independently of A.
        for bad_b, message in (
            (replace(point_b, complete=False), "incomplete recovery artifact"),
            (replace(point_b, corrupt=True), "corrupt recovery artifact"),
            (replace(point_b, key_available=False), "missing recovery key"),
            (replace(point_b, source="wrong-target"), "existing destination is not the exact target"),
        ):
            before = deepcopy(target)
            with self.assertRaisesRegex(ProtocolFailure, message):
                restore_existing(self.case, target, point_a, bad_b)
            self.assertEqual(target, before)

        target.state = deepcopy(point_b.state)
        target.active = True
        target.complete = True
        with self.assertRaisesRegex(ProtocolFailure, "after replacement"):
            restore_existing(self.case, target, point_a, point_b, failure_stage="after_replace")
        self.assertFalse(target.active or target.complete)
        recover_preserved(target, point_b)
        self.assertIn("chat-b-only", target.state["webui_db"]["chats"])
        restore_existing(self.case, target, point_a, point_b)
        self.assertEqual(target.state, point_a.state)
        self.assertTrue(target.active and target.complete)

    def test_shared_protocol_covers_negative_cases_and_target_boundaries(self):
        report = run_protocol(self.case)
        self.assertEqual(report["new"]["status"], "passed")
        self.assertTrue(report["new"]["source_isolated"])
        self.assertTrue(report["existing"]["b_point_retained"])
        self.assertIn("wrong-target", report["negative_cases"])
        self.assertIn("corrupt-artifact", report["negative_cases"])
        self.assertIn("incomplete-artifact", report["negative_cases"])
        self.assertIn("missing-key", report["negative_cases"])
        self.assertIn("partial-recovery", report["negative_cases"])
        self.assertEqual(set(run_negative_cases(self.case)), set(report["negative_cases"]))

    def test_coverage_never_promotes_unreadable_live_evidence(self):
        report = coverage.build_report()
        row = next(product for product in report["products"] if product["product"] == "open-webui")
        native = row["methods"]["native"]
        guest = row["methods"]["pbs_guest"]
        project_managed = row["methods"]["project_managed"]
        self.assertEqual(native["availability"], "declared")
        self.assertIn("complete named Open WebUI data_path", native["recovery_unit"])
        self.assertEqual(guest["availability"], "applicable")
        self.assertIn("shared ai LXC guest", guest["recovery_unit"])
        self.assertEqual(project_managed["availability"], "not_declared")
        self.assertEqual(row["version"], "main")
        self.assertEqual(row["credentials"]["status"], "unknown")
        self.assertEqual(row["external_data"][0]["status"], "unknown")
        self.assertIn("named Ollama and/or LiteLLM", row["external_data"][0]["description"])
        self.assertEqual(native["evidence"]["schedule"]["status"], "configured")
        self.assertEqual(
            {native["evidence"][key]["status"] for key in ("artifact", "integrity", "external_data", "restore")},
            {"unknown"},
        )
        self.assertNotIn(
            "verified",
            {item["status"] for item in native["evidence"].values()},
        )


if __name__ == "__main__":
    unittest.main()
