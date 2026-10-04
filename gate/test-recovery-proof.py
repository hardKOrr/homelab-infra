#!/usr/bin/env python3
"""Run production proof orchestration and real Ntfy assertions without providers."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ansible/files/recovery"))
spec = importlib.util.spec_from_file_location(
    "proof", ROOT / "ansible/files/recovery/proof.py"
)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def request(**values):
    return dict(
        app="ntfy",
        instance="ntfy",
        vmid="420",
        method="auto",
        execute=True,
        disposable=True,
        workloads="ntfy",
        destination="existing",
        backup_storage="pbs-fixture",
        target_storage="local",
        **values,
    )


class RecordingSeams:
    """Independent fake provider boundaries, including retained artifact content."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail
        self.points = {}
        self.state = set()
        self.next = 0
        self.changed = 0
        self.assert_bad = False
        self.warnings = False
        self.observation = dict(
            guest=dict(
                vmid=420,
                type="lxc",
                name="ntfy",
                node="fixture",
                status="running",
                tags="_+lab;_-debian;_ntfy",
            ),
            config=dict(
                hostname="ntfy",
                tags="_+lab;_-debian;_ntfy",
                onboot=1,
                net0="name=eth0,bridge=fixture,ip=192.0.2.2/24",
            ),
            artifacts=[],
            targets=[],
            units=[{"vmid": 420}],
            source_storages=[{"storage": "pbs-fixture", "type": "pbs", "active": 1}],
            storages=[{"storage": "local", "active": 1}],
        )

    def inspect(self, **values):
        self.calls.append(("inspect", values))
        result = copy.deepcopy(self.observation)
        result["artifacts"] = [{"volid": name} for name in self.points]
        if "vmid" in values:
            result["guest"].update(vmid=int(values["vmid"]), status="stopped")
            result["config"].update(
                hostname="new-fixture",
                tags="_+lab;_new-fixture",
                onboot=0,
                net0="bridge=isolated,link_down=1,ip=192.0.2.3/24,hwaddr=02:00:00:00:00:03",
            )
        return result

    def run(self, playbook, **values):
        self.calls.append((playbook, values))
        if self.fail and self.fail in playbook:
            raise proof.Refused("Injected owning seam failure.")
        observations = []
        changed = self.changed if "/apps/" in playbook else 0
        if playbook.endswith("proof-serving.yml"):
            observations = [dict(serving=True, url="http://192.0.2.2")]
        if playbook.endswith("proof-ntfy.yml"):
            phase = values["proof_phase"]
            if phase in ("A", "B"):
                self.state.add(phase)
            observations = [
                dict(
                    serving=True,
                    a_present="A" in self.state,
                    b_present="B" in self.state,
                    b_absent="B" not in self.state and not self.assert_bad,
                )
            ]
        if playbook.endswith("backup-guest.yml"):
            self.next += 1
            kind = "vm" if self.observation["guest"]["type"] == "qemu" else "ct"
            tool = "qemu" if kind == "vm" else "lxc"
            self.points[f"backup/{kind}/420/vzdump-{tool}-420-{self.next}"] = set(
                self.state
            )
        if playbook.endswith("restore-guest.yml") and values["overwrite"]:
            assert values["pre_restore_point"] != values["recovery_point"]
            assert "B" in self.points[values["pre_restore_point"]]
            self.state = set(self.points[values["recovery_point"]])
        return dict(observations=observations, stats={"fixture": {"changed": changed}})


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.inputs, _, _ = proof.load_inputs()
        self.backend = RecordingSeams()

    def dispatch(self, data=None):
        return proof.dispatch(data or request(), self.inputs, self.backend)

    def test_existing_sequence_runs_real_dispatcher_and_retains_independent_b(self):
        report, status = self.dispatch()
        self.assertEqual(status, 0, report)
        self.assertTrue(report["durable_data"])
        self.assertFalse(report["live_verified"])
        self.assertEqual(report["onboot"], 1)
        self.assertIn("B", self.backend.points[report["artifacts"]["B"]])
        self.assertEqual(self.backend.state, {"A"})
        calls = [
            (Path(p).name, v.get("proof_phase", v.get("overwrite")))
            for p, v in self.backend.calls
            if p != "inspect"
        ]
        self.assertEqual(
            calls,
            [
                ("ntfy.yml", None),
                ("ntfy.yml", None),
                ("proof-serving.yml", None),
                ("proof-ntfy.yml", "A"),
                ("backup-guest.yml", None),
                ("proof-artifact.yml", None),
                ("proof-ntfy.yml", "B"),
                ("backup-guest.yml", None),
                ("proof-artifact.yml", None),
                ("restore-guest.yml", False),
                ("restore-guest.yml", True),
                ("proof-ntfy.yml", "verify"),
            ],
        )

    def test_existing_vm_uses_native_vm_artifacts_and_same_owning_restore(self):
        self.backend.observation["guest"]["type"] = "qemu"
        self.backend.observation["config"]["name"] = self.backend.observation[
            "config"
        ].pop("hostname")
        report, status = self.dispatch()
        self.assertEqual(status, 0, report)
        self.assertTrue(
            report["artifacts"]["A"].startswith("backup/vm/420/vzdump-qemu-420-")
        )
        restores = [v for p, v in self.backend.calls if p.endswith("restore-guest.yml")]
        self.assertEqual(len(restores), 2)
        self.assertEqual(restores[-1]["target_vmid"], "420")

    def test_plan_never_mutates(self):
        data = request()
        data["execute"] = False
        data["workloads"] = ""
        report, status = self.dispatch(data)
        self.assertEqual(status, 0)
        self.assertFalse(report["mutation"])
        self.assertEqual(report["workloads"], ["ntfy"])
        self.assertFalse(report["scope_acknowledged"])
        self.assertEqual(self.backend.calls, [("inspect", {"proof_selector": "_ntfy"})])

    def test_unsupported_resolves_before_even_guest_read(self):
        for app in [
            "home-assistant",
            "actual-budget",
            "searxng",
            "k3s-cluster",
            "opnsense",
            "pbs",
            "vaultwarden",
        ]:
            with self.subTest(app=app):
                data = request()
                data["app"] = app
                report, status = self.dispatch(data)
                self.assertEqual(status, 1, report)
                self.assertEqual(self.backend.calls, [])
        for method in ["native", "project_managed", "rebuild"]:
            data = request()
            data["method"] = method
            self.assertEqual(self.dispatch(data)[1], 1)
            self.assertEqual(self.backend.calls, [])

    def test_guest_scope_refusals_precede_deployment(self):
        for mutation in [
            lambda g: g["guest"].update(tags="_ntfy"),
            lambda g: g["guest"].update(tags="_+lab;_ntfy;_sibling"),
            lambda g: g["guest"].update(tags="_+lab;_ntfy;_rundeck"),
            lambda g: g["guest"].update(tags="_+lab;_ntfy;_.cluster+fixture"),
            lambda g: g["config"].update(mp0="/external,mp=/data"),
            lambda g: g["config"].update(hostpci0="fixture"),
            lambda g: g["guest"].update(status="stopped"),
        ]:
            self.backend = RecordingSeams()
            mutation(self.backend.observation)
            report, status = self.dispatch()
            self.assertEqual(status, 1, report)
            self.assertTrue(all(p == "inspect" for p, _ in self.backend.calls))

    def test_shared_scope_is_disclosed_exactly(self):
        self.backend.observation["guest"]["tags"] += ";_sibling"
        data = request()
        data["workloads"] = "ntfy,sibling"
        self.assertEqual(self.dispatch(data)[0]["workloads"], ["ntfy", "sibling"])

    def test_no_convergence_stops_before_fixture(self):
        self.backend.changed = 1
        report, status = self.dispatch()
        self.assertEqual(status, 1)
        self.assertFalse(self.backend.points)
        self.assertEqual(report["stage"], "deploy")

    def test_serving_only_never_claims_durable_data(self):
        del self.inputs["ntfy"]["defaults"]["recovery"]["drill"]
        # Serving-only mode has no A/B data, so the fake backup seam need not assert it.
        original = self.backend.run

        def run(playbook, **values):
            if playbook.endswith("restore-guest.yml") and values["overwrite"]:
                self.backend.points[values["pre_restore_point"]].add("B")
            return original(playbook, **values)

        self.backend.run = run
        report, status = self.dispatch()
        self.assertEqual(status, 0, report)
        self.assertFalse(report["durable_data"])
        self.assertEqual(report["fixture"], "serving-only")
        self.assertNotIn("A-present", report["assertions"])

    def test_warnings_bound_complete_evidence(self):
        self.backend.warnings = True
        report, status = self.dispatch()
        self.assertEqual(status, 0)
        self.assertTrue(report["sequence_complete"])
        self.assertTrue(report["warnings_present"])
        self.assertFalse(report["complete"])

    def test_failed_assertions_cannot_claim_recovery(self):
        self.backend.assert_bad = True
        report, status = self.dispatch()
        self.assertEqual(status, 1)
        self.assertFalse(report["complete"])
        self.assertEqual(set(report["artifacts"]), {"A", "B"})

    def test_failed_seams_report_stage_and_stop_without_cleanup_or_retry(self):
        for seam, stage in [
            ("apps/", "deploy"),
            ("proof-ntfy", "fixture-A"),
            ("backup-guest", "backup-A"),
            ("restore-guest", "restore-plan"),
        ]:
            self.backend = RecordingSeams(fail=seam)
            report, status = self.dispatch()
            self.assertEqual(status, 1)
            self.assertEqual(report["stage"], stage)
            self.assertFalse(report["complete"])
            self.assertIn("Retain A and B", report["retry"])

    def test_new_route_inspects_only_stopped_destination_after_restore(self):
        data = request()
        data.update(
            destination="new",
            target_vmid="520",
            target_node="fixture",
            target_name="new-fixture",
            target_tags="_+lab;_new-fixture",
            target_network="bridge=isolated,link_down=1,ip=192.0.2.3/24,hwaddr=02:00:00:00:00:03",
        )
        report, status = self.dispatch(data)
        self.assertEqual(status, 0, report)
        self.assertFalse(report["complete"])
        self.assertFalse(report["durable_data"])
        self.assertEqual(self.backend.calls[-1], ("inspect", {"vmid": "520"}))
        self.assertIn("onboot=0", report["target_state"])

    def test_duplicate_instance_and_unavailable_storage_are_refused(self):
        for field, value in [
            ("units", [{"vmid": 420}, {"vmid": 421}]),
            ("storages", []),
        ]:
            self.backend = RecordingSeams()
            self.backend.observation[field] = value
            self.assertEqual(self.dispatch()[1], 1)
            self.assertEqual(len(self.backend.calls), 1)

    def test_readability_failure_precedes_b_fixture(self):
        self.backend.fail = "proof-artifact"
        report, status = self.dispatch()
        self.assertEqual(status, 1)
        self.assertEqual(report["stage"], "backup-A")
        self.assertEqual(set(report["artifacts"]), {"A"})
        self.assertNotIn("B", self.backend.state)

    def test_restore_execution_failure_retains_b_without_verification(self):
        original = self.backend.run

        def run(playbook, **values):
            if playbook.endswith("restore-guest.yml") and values["overwrite"]:
                raise proof.Refused("Injected restore worker failure.")
            return original(playbook, **values)

        self.backend.run = run
        report, status = self.dispatch()
        self.assertEqual(status, 1)
        self.assertEqual(report["stage"], "restore-execute")
        self.assertEqual(set(report["artifacts"]), {"A", "B"})
        self.assertNotIn(
            "verify", [v.get("proof_phase") for _, v in self.backend.calls]
        )

    def test_identity_mismatch_is_not_a_successful_proof(self):
        original = self.backend.run

        def run(playbook, **values):
            result = original(playbook, **values)
            if playbook.endswith("restore-guest.yml") and values["overwrite"]:
                self.backend.observation["config"]["onboot"] = 0
            return result

        self.backend.run = run
        report, status = self.dispatch()
        self.assertEqual(status, 1)
        self.assertEqual(report["stage"], "verify")
        self.assertFalse(report["complete"])

    def test_new_collision_and_nonisolated_interface_refused_before_mutation(self):
        data = request()
        data.update(
            destination="new",
            target_vmid="520",
            target_node="fixture",
            target_name="new-fixture",
            target_tags="_+lab;_new-fixture",
            target_network="bridge=fixture",
        )
        self.assertEqual(self.dispatch(data)[1], 1)
        self.assertEqual(len(self.backend.calls), 1)

    def test_new_interface_identity_and_isolation_cannot_be_bypassed(self):
        data = request()
        data.update(
            destination="new",
            target_vmid="520",
            target_node="fixture",
            target_name="new-fixture",
            target_tags="_+lab;_new-fixture",
            target_network="bridge=isolated,link_down=1,ip=192.0.2.3/24,hwaddr=02:00:00:00:00:03",
        )
        for network in [
            "bridge=isolated,link_down=10,ip=192.0.2.3/24,hwaddr=02:00:00:00:00:03",
            "bridge=isolated,link_down=1,ip=192.0.2.2/25,hwaddr=02:00:00:00:00:03",
            "bridge=isolated,link_down=1,ip=dhcp,hwaddr=02:00:00:00:00:03",
        ]:
            self.backend = RecordingSeams()
            data["target_network"] = network
            self.assertEqual(self.dispatch(data)[1], 1)
            self.assertEqual(len(self.backend.calls), 1)

    def test_artifact_freshness_rejects_stale_and_ambiguous_points(self):
        before = {"artifacts": [{"volid": "backup/ct/420/vzdump-lxc-420-old"}]}
        for points in [
            before["artifacts"],
            [{"volid": "backup/ct/421/vzdump-lxc-421-A"}],
            [
                {"volid": "backup/ct/420/vzdump-lxc-420-A"},
                {"volid": "backup/ct/420/vzdump-lxc-420-B"},
            ],
        ]:
            with self.assertRaises(proof.Refused):
                proof.fresh_artifact(before, {"artifacts": points}, "420", "lxc")


class AdapterTests(unittest.TestCase):
    def test_real_child_callback_only_transports_observations_and_degradation(self):
        import os

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transport.yml"
            path.write_text(
                yaml.safe_dump(
                    [
                        dict(
                            name="Private transport fixture",
                            hosts="localhost",
                            connection="local",
                            gather_facts=False,
                            tasks=[
                                {
                                    "name": "Hidden input",
                                    "ansible.builtin.debug": {"msg": "sentinel-secret"},
                                    "no_log": True,
                                },
                                {
                                    "name": "Unselected output",
                                    "ansible.builtin.debug": {
                                        "msg": "sentinel-private-diagnostic"
                                    },
                                },
                                {
                                    "name": "Selected output",
                                    "ansible.builtin.debug": {
                                        "msg": {"proof_observation": {"serving": True}}
                                    },
                                },
                                {
                                    "name": "Ignored failure",
                                    "ansible.builtin.command": {"argv": ["/bin/false"]},
                                    "ignore_errors": True,
                                },
                            ],
                        )
                    ]
                )
            )
            env = dict(
                os.environ,
                ANSIBLE_CONFIG=str(ROOT / "ansible/ansible.cfg"),
                ANSIBLE_STDOUT_CALLBACK="recovery_proof",
                ANSIBLE_CALLBACKS_ENABLED="",
            )
            result = subprocess.run(
                [
                    str(Path(sys.executable).with_name("ansible-playbook")),
                    "-i",
                    "localhost,",
                    str(path),
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["observations"], [{"serving": True}])
            self.assertTrue(report["failed"])
            self.assertEqual(report["stats"]["localhost"]["ignored"], 1)
            self.assertNotIn("sentinel", result.stdout)

    def test_real_ntfy_tasks_assert_a_survives_and_b_disappears(self):
        records = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                records.append(
                    {
                        "event": "message",
                        "message": self.rfile.read(
                            int(self.headers["Content-Length"])
                        ).decode(),
                    }
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    ("\n".join(json.dumps(row) for row in records)).encode()
                )

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            play = yaml.safe_load(
                (ROOT / "ansible/playbooks/maintenance/proof-ntfy.yml").read_text()
            )[0]
            play["connection"] = "local"
            play["tasks"] = play["tasks"][
                1:
            ]  # Private input loader replaced, real assertion tasks unchanged.
            play["vars"] = dict(
                proof_url=f"http://127.0.0.1:{server.server_port}",
                proof_topic="fixture",
                proof_run="unique",
                homelabinfra_infra={"notifications": {"token": "fixture-only"}},
            )
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "adapter.yml"
                path.write_text(yaml.safe_dump([play]))

                def phase(name):
                    return subprocess.run(
                        [
                            str(Path(sys.executable).with_name("ansible-playbook")),
                            "-i",
                            "localhost,",
                            str(path),
                            "-e",
                            "proof_phase=" + name,
                        ],
                        capture_output=True,
                        text=True,
                    ).returncode

                self.assertEqual(phase("A"), 0)
                self.assertEqual(phase("B"), 0)
                self.assertNotEqual(
                    phase("verify"), 0
                )  # A green service with B present is insufficient.
                records.pop()
                self.assertEqual(phase("verify"), 0)
                records.clear()
                self.assertNotEqual(phase("verify"), 0)  # A absent fails too.
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
