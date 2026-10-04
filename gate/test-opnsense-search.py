#!/usr/bin/env python3
"""Execute source-owned wiring against a loopback-only, credential-free API fixture."""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WIRING = ROOT / "ansible/tasks/wiring/opnsense.yml"
PRIVATE_MARKER = "fixture-protected-provider-detail"


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                state = cls.state
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["requests"].append((self.path, body))
                expected = "Basic " + base64.b64encode(b"fixture-key:fixture-secret").decode()
                if self.headers.get("Authorization") != expected:
                    self.send_error(401)
                    return
                status, response, content_type = 200, {}, "application/json"
                if self.path.endswith("/searchHostOverride"):
                    mode = state["mode"]
                    if isinstance(mode, int):
                        status, response = mode, {"error": PRIVATE_MARKER}
                    elif mode == "html":
                        response, content_type = PRIVATE_MARKER, "text/html"
                    elif mode == "missing-rows":
                        response = {"error": PRIVATE_MARKER}
                    elif mode == "invalid-rows":
                        response = {"rows": [PRIVATE_MARKER]}
                    elif mode == "transport":
                        self.close_connection = True
                        return
                    else:
                        response = {"rows": state["rows"], "rowCount": 1000,
                                    "total": len(state["rows"]), "current": 1}
                elif self.path.endswith("/addHostOverride"):
                    state["rows"] = [{"uuid": "fixture-uuid", **body["host"]}]
                    response = {"result": "saved"}
                elif "/setHostOverride/" in self.path:
                    state["rows"] = [{"uuid": "fixture-uuid", **body["host"]}]
                    response = {"result": "saved"}
                elif self.path.endswith("/reconfigure"):
                    response = {"status": "ok"}
                else:
                    self.send_error(404)
                    return
                encoded = (json.dumps(response) if content_type == "application/json"
                           else response).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("X-Fixture-Private", PRIVATE_MARKER)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.state.clear()
        self.state.update(mode="ok", requests=[], rows=[])

    def run_wiring(self, *, provider="opnsense", missing_host=False):
        dns = {"provider": provider,
               "host": f"http://127.0.0.1:{self.server.server_port}",
               "api_key": "fixture-key", "api_secret": "fixture-secret"}
        if missing_host:
            dns.pop("host")
        infra = {"dns": dns, "reverse_proxy": {"host": "http://192.0.2.2"}}
        if provider is None:
            infra = {}
        play = [{"name": "Provider-free OPNsense wiring regression", "hosts": "localhost",
                 "connection": "local", "gather_facts": False,
                 "vars": {"homelabinfra_infra": infra,
                          "wiring_domain": "caddy.example.test",
                          "wiring_upstream_host": "192.0.2.3"},
                 "tasks": [{"ansible.builtin.include_tasks": str(WIRING)}]}]
        with tempfile.TemporaryDirectory(prefix="opnsense-search-") as tmp:
            path = Path(tmp) / "play.yml"
            path.write_text(yaml.safe_dump(play))
            env = {**os.environ, "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
                   "ANSIBLE_LOCAL_TEMP": tmp + "/local", "ANSIBLE_REMOTE_TEMP": tmp + "/remote",
                   "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                   "ANSIBLE_DISPLAY_OK_HOSTS": "1", "ANSIBLE_DISPLAY_SKIPPED_HOSTS": "1",
                   "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
            result = subprocess.run(
                [str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"),
                 "-i", "localhost,", str(path)], env=env,
                capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr
        for protected in (PRIVATE_MARKER, "fixture-key", "fixture-secret"):
            self.assertNotIn(protected, output)
        return result.returncode, output

    def test_add_converge_and_update(self):
        code, output = self.run_wiring()
        self.assertEqual(code, 0, output)
        self.assertEqual([p.rsplit("/", 1)[-1] for p, _ in self.state["requests"]],
                         ["searchHostOverride", "addHostOverride", "reconfigure"])
        self.assertEqual(self.state["requests"][0][1],
                         {"current": 1, "rowCount": 1000, "searchPhrase": "caddy"})
        self.assertEqual(self.state["rows"][0]["server"], "192.0.2.2")
        self.state["requests"].clear()
        code, output = self.run_wiring()
        self.assertEqual(code, 0, output)
        self.assertIn("changed=0", output)
        self.assertEqual(len(self.state["requests"]), 1)
        self.state["rows"][0]["server"] = "192.0.2.99"
        self.state["requests"].clear()
        code, output = self.run_wiring()
        self.assertEqual(code, 0, output)
        self.assertEqual([p.rsplit("/", 1)[-1] for p, _ in self.state["requests"]],
                         ["searchHostOverride", "fixture-uuid", "reconfigure"])

    def test_provider_noop(self):
        for provider in (None, "none", "pihole"):
            with self.subTest(provider=provider):
                code, output = self.run_wiring(provider=provider)
                self.assertEqual(code, 0, output)
                self.assertEqual(self.state["requests"], [])

    def test_protected_failures_stop_before_mutation(self):
        for mode, category, status in (
            (401, "authentication-or-authorization", 401),
            (403, "authentication-or-authorization", 403),
            (500, "http-status", 500),
            ("transport", "transport", -1),
            ("html", "response-shape", 200),
            ("missing-rows", "response-shape", 200),
            ("invalid-rows", "response-shape", 200),
        ):
            with self.subTest(mode=mode):
                self.setUp()
                self.state["mode"] = mode
                code, output = self.run_wiring()
                self.assertNotEqual(code, 0, output)
                self.assertIn(f"category={category}; status={status}.", " ".join(output.split()))
                self.assertEqual(len(self.state["requests"]), 1)

    def test_templating_failure_is_not_called_transport(self):
        code, output = self.run_wiring(missing_host=True)
        self.assertNotEqual(code, 0, output)
        self.assertIn("category=pre-request-or-module; status=0.", " ".join(output.split()))
        self.assertEqual(self.state["requests"], [])

    def test_secret_bearing_search_stays_protected(self):
        block = yaml.safe_load(WIRING.read_text())[0]["block"]
        search = next(t for t in block if "block" in t)["block"][0]
        self.assertTrue(search["no_log"])
        self.assertFalse(search["changed_when"])
        self.assertTrue(search["ansible.builtin.uri"]["force_basic_auth"])


if __name__ == "__main__":
    unittest.main()
