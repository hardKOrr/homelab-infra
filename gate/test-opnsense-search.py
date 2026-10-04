#!/usr/bin/env python3
"""Execute source-owned wiring against a loopback-only, credential-free API fixture."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from ansible.utils.unsafe_proxy import wrap_var
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


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

            def do_CONNECT(self):
                cls.state["proxy_requests"] += 1
                self.send_response(407)
                self.send_header("Proxy-Authenticate", PRIVATE_MARKER)
                self.end_headers()

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
                    elif mode == "timeout":
                        time.sleep(2)
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
        cls.tls_dir = tempfile.TemporaryDirectory(prefix="opnsense-tls-")
        cert, key = Path(cls.tls_dir.name) / "fixture.crt", Path(cls.tls_dir.name) / "fixture.key"
        # cryptography is part of the configured Ansible venv; no optional CLI
        # dependency should prevent even the HTTP-only tests from starting.
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, PRIVATE_MARKER)])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder()
                       .subject_name(subject).issuer_name(subject)
                       .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - timedelta(minutes=1))
                       .not_valid_after(now + timedelta(days=1))
                       .add_extension(x509.SubjectAlternativeName([
                           x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                       .sign(private_key, hashes.SHA256()))
        key.write_bytes(private_key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.TraditionalOpenSSL,
                        serialization.NoEncryption()))
        key.chmod(0o600)
        cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        cls.tls_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        cls.tls_server.socket = context.wrap_socket(cls.tls_server.socket, server_side=True)
        cls.tls_thread = threading.Thread(target=cls.tls_server.serve_forever, daemon=True)
        cls.tls_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tls_server.shutdown()
        cls.tls_server.server_close()
        cls.tls_thread.join()
        cls.tls_dir.cleanup()

    def setUp(self):
        self.state.clear()
        self.state.update(mode="ok", requests=[], rows=[], proxy_requests=0)

    def run_wiring(self, *, provider="opnsense", missing_host=False, host=None,
                   validate_certs=False, environment=None, uri_defaults=None):
        dns = {"provider": provider,
               "host": f"http://127.0.0.1:{self.server.server_port}",
               "api_key": "fixture-key", "api_secret": "fixture-secret"}
        dns.update(host=host or dns["host"], validate_certs=validate_certs)
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
        if uri_defaults:
            play[0]["module_defaults"] = {"ansible.builtin.uri": uri_defaults}
        with tempfile.TemporaryDirectory(prefix="opnsense-search-") as tmp:
            path = Path(tmp) / "play.yml"
            path.write_text(yaml.safe_dump(play))
            # Fixture traffic must never inherit an operator proxy/CA route.
            env = {k: v for k, v in os.environ.items()
                   if k.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
                   and k not in {"SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE",
                                 "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"}}
            env.update({"ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
                   "ANSIBLE_LOCAL_TEMP": tmp + "/local", "ANSIBLE_REMOTE_TEMP": tmp + "/remote",
                   "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                   "ANSIBLE_DISPLAY_OK_HOSTS": "1", "ANSIBLE_DISPLAY_SKIPPED_HOSTS": "1",
                   "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
            env.update(environment or {})
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

    def independent_search(self, url):
        """Retained observer helper mechanics, using only local fixture inputs.

        urllib default opener/redirects, unverified SSL context and 15s timeout.
        No claim about the unrecorded live proxy environment or interpreter patch.
        """
        request = urllib.request.Request(
            url + "/api/unbound/settings/searchHostOverride",
            data=json.dumps({"current": 1, "rowCount": 1000, "searchPhrase": "caddy"}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Basic " +
                     base64.b64encode(b"fixture-key:fixture-secret").decode()}, method="POST")
        with urllib.request.urlopen(request, context=ssl._create_unverified_context(),
                                    timeout=15) as response:
            return response.status, json.load(response)

    def test_https_independent_and_actual_uri_defaults(self):
        from unittest.mock import patch

        url = f"https://127.0.0.1:{self.tls_server.server_port}"
        # A clean, local-only environment is a fixture constraint, not a live fact.
        clean = {k: v for k, v in os.environ.items()
                 if k.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
                 and k not in {"SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"}}
        clean.update(no_proxy="127.0.0.1", NO_PROXY="127.0.0.1")
        with patch.dict(os.environ, clean, clear=True):
            status, response = self.independent_search(url)
        self.assertEqual(status, 200)
        self.assertEqual(response["rows"], [])
        self.state["requests"].clear()
        code, output = self.run_wiring(host=url)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.state["requests"]), 3)
        self.setUp()
        # This deliberately different fixture setting demonstrates why independent
        # success does not prove the URI's effective TLS inputs. Production is unchanged.
        code, output = self.run_wiring(host=url, validate_certs=True)
        self.assertNotEqual(code, 0, output)
        self.assertIn("transport_signature=tls-certificate.", output)
        self.assertEqual(self.state["requests"], [])
        # Asking HTTPS of a plaintext local HTTP listener exercises the real
        # SSL protocol error path without weakening any production TLS setting.
        self.setUp()
        code, output = self.run_wiring(host=f"https://127.0.0.1:{self.server.server_port}")
        self.assertNotEqual(code, 0, output)
        self.assertIn("transport_signature=tls-protocol.", output)
        self.assertEqual(self.state["requests"], [])

    def test_actual_uri_transport_signatures(self):
        cases = [
            ("transport", {}, {}, "peer-closed"),
            ("timeout", {}, {"timeout": 1}, "timeout"),
        ]
        for mode, environment, defaults, signature in cases:
            with self.subTest(signature=signature):
                self.setUp()
                self.state["mode"] = mode
                code, output = self.run_wiring(environment=environment, uri_defaults=defaults)
                self.assertNotEqual(code, 0, output)
                self.assertIn(f"transport_signature={signature}.", output)
                self.assertEqual(len(self.state["requests"]), 1)
        self.setUp()
        # URI urllib honours a proxy route inherited by its module process. An
        # independent helper with a different environment can still return 200.
        # Keep the port exclusively bound but not listening throughout the probe:
        # connects are refused, and another process cannot take the selected port.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as closed:
            closed.bind(("127.0.0.1", 0))
            port = closed.getsockname()[1]
            code, output = self.run_wiring(environment={
                "http_proxy": f"http://127.0.0.1:{port}", "no_proxy": "", "NO_PROXY": ""})
            self.assertNotEqual(code, 0, output)
            self.assertIn("transport_signature=connection-refused.", output)
            self.assertEqual(self.state["requests"], [])
        self.setUp()
        code, output = self.run_wiring(
            host=f"https://127.0.0.1:{self.tls_server.server_port}",
            environment={"https_proxy": f"http://127.0.0.1:{self.server.server_port}",
                         "no_proxy": "", "NO_PROXY": ""})
        self.assertNotEqual(code, 0, output)
        self.assertIn("transport_signature=proxy-tunnel-rejected.", output)
        self.assertEqual(self.state["requests"], [])
        self.assertEqual(self.state["proxy_requests"], 1)

    def test_allowlist_never_repeats_arbitrary_failure_text(self):
        tasks = yaml.safe_load(WIRING.read_text())[0]["block"]
        rescue = next(t for t in tasks if "rescue" in t)["rescue"][0]
        for status, message, expected in (
            (-1, f"Request failed: <urlopen error [Errno -2] Name or service not known {PRIVATE_MARKER}>",
             "name-resolution"),
            (-1, "Request failed: <urlopen error [SSL: WRONG_VERSION_NUMBER] " + PRIVATE_MARKER + ">",
             "tls-protocol"),
            (-1, "Request failed: <urlopen error [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] " + PRIVATE_MARKER + ">",
             "tls-handshake"),
            (-1, PRIVATE_MARKER + " [SSL: CERTIFICATE_VERIFY_FAILED]", "unclassified"),
            (-1, {"protected": PRIVATE_MARKER}, "unclassified"),
            (500, "Request failed: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] " + PRIVATE_MARKER,
             "not-transport"),
        ):
            with self.subTest(expected=expected):
                variables = {"ansible_failed_result": wrap_var({"status": status, "msg": message,
                             "exception": PRIVATE_MARKER, "invocation": {"protected": PRIVATE_MARKER}}),
                             "ansible_failed_task": {"name": "Wire OPNsense | Search existing host overrides"}}
                variables.update(rescue["vars"])
                output = Templar(loader=DataLoader(), variables=variables).template(
                    rescue["ansible.builtin.fail"]["msg"])
                self.assertIn(f"transport_signature={expected}.", output)
                self.assertNotIn(PRIVATE_MARKER, output)

    def test_secret_bearing_search_stays_protected(self):
        block = yaml.safe_load(WIRING.read_text())[0]["block"]
        search = next(t for t in block if "block" in t)["block"][0]
        self.assertTrue(search["no_log"])
        self.assertFalse(search["changed_when"])
        self.assertTrue(search["ansible.builtin.uri"]["force_basic_auth"])


if __name__ == "__main__":
    unittest.main()
