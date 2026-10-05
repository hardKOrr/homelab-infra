#!/usr/bin/env python3
"""Exercise enrollment with the actual bootstrap CLI and a disposable HTTPS vault.

No lab inputs are used. Docker, npm and OpenSSL are required. All client output is
captured, and failures report only the step (never credentials, sessions or API logs).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import ssl
import sys
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]


def run(args, *, env=None, step, success=True):
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=180)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{step}: unexpected exit status (output withheld)")
    return result.stdout.strip()


def check():
    bootstrap = (ROOT / "rundeck/bootstrap-rundeck.sh").read_text()
    packages = re.findall(r"^npm install -g --silent (@bitwarden/cli@[^\s]+)", bootstrap, re.M)
    assert len(packages) == 1, "bootstrap must install an explicit CLI compatibility pin"
    # Execute the shipped install/check block with a successful npm and a shadowing
    # bw. The reported executable must match the package pin, not just exist on PATH.
    block = bootstrap[bootstrap.index('say "Bitwarden CLI"'):
                      bootstrap.index('# -- rundeck config')]
    with tempfile.TemporaryDirectory(prefix="homelab-cli-version-") as directory:
        work = Path(directory)
        for name, body in {
            "npm": "exit 0",
            "bw": 'printf "%s\\n" "$FIXTURE_BW_VERSION"; exit "$FIXTURE_BW_EXIT"',
        }.items():
            executable = work / name
            executable.write_text("#!/bin/sh\n" + body + "\n")
            executable.chmod(0o700)
        expected = packages[0].rsplit("@", 1)[1]
        for version, exit_code, succeeds in (
            (expected, "0", True), ("stale-version", "0", False),
            ("", "0", False), (expected, "2", False),
        ):
            result = subprocess.run(
                ["/bin/bash", "-c", 'set -euo pipefail\nsay() { :; }\n' + block + '\necho continued'],
                env={"PATH": directory, "FIXTURE_BW_VERSION": version, "FIXTURE_BW_EXIT": exit_code},
                capture_output=True, text=True, timeout=30,
            )
            assert (result.returncode == 0) == succeeds, "bootstrap accepted an incompatible CLI"
            assert ("continued" in result.stdout) == succeeds, "bootstrap continued after a failed version check"
    container = None
    proxy = None
    with tempfile.TemporaryDirectory(prefix="homelab-cli-test-") as directory:
        work = Path(directory)
        try:
            run(["npm", "install", "--prefix", directory, "--no-audit", "--no-fund", packages[0]],
                step="install pinned CLI")
            labels = ["--label", "homelab.test=vaultwarden-cli"]
            if os.environ.get("AO_SESSION_ID"):
                labels += ["--label", f"ao.session={os.environ['AO_SESSION_ID']}"]
            container = run([
                "docker", "run", "-d", *labels, "-p", "127.0.0.1::80",
                "-e", "ROCKET_PORT=80", "-e", "SIGNUPS_ALLOWED=true",
                "-e", "I_REALLY_WANT_VOLATILE_STORAGE=true", "vaultwarden/server:1.37.3",
            ], step="start disposable vault")
            port = run(["docker", "port", container, "80/tcp"], step="resolve fixture port").split(":")[-1]
            backend = f"http://127.0.0.1:{port}"
            for _ in range(60):
                try:
                    with urllib.request.urlopen(backend + "/alive", timeout=1):
                        break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.25)
            else:
                raise AssertionError("disposable vault did not become ready")

            certificate, private = work / "ca.pem", work / "key.pem"
            run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", str(private), "-out", str(certificate), "-days", "1",
                 "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                step="create local HTTPS trust")

            class Proxy(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def dispatch(self):
                    body = self.rfile.read(int(self.headers.get("Content-Length", 0))) or None
                    headers = {k: v for k, v in self.headers.items()
                               if k.lower() not in ("host", "content-length")}
                    request = urllib.request.Request(backend + self.path, data=body,
                                                     headers=headers, method=self.command)
                    try:
                        response = urllib.request.urlopen(request, timeout=30)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        data = response.read()
                        self.send_response(response.status)
                        self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)

                do_GET = do_POST = do_PUT = dispatch

            proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, private)
            proxy.socket = context.wrap_socket(proxy.socket, server_side=True)
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            server = f"https://localhost:{proxy.server_port}"
            inputs = {
                "BW_SERVER": server, "SSL_CERT_FILE": str(certificate),
                "VAULTWARDEN_OWNER_EMAIL": "owner@example.com",
                "VAULTWARDEN_OWNER_PASSWORD": secrets.token_urlsafe(32),
                "VAULTWARDEN_AUTOMATION_EMAIL": "automation@example.com",
                "VAULTWARDEN_AUTOMATION_PASSWORD": secrets.token_urlsafe(32),
            }
            # Execute the unchanged enrollment entry point over verified HTTPS.
            enrollment_env = {**os.environ, **inputs}
            helper = [sys.executable, str(ROOT / "ansible/scripts/vaultwarden-enroll.py")]
            first = json.loads(run(helper, env=enrollment_env, step="enrollment ceremony"))
            second = json.loads(run(helper, env=enrollment_env, step="convergent enrollment"))
            assert first == second, "rerun changed staged API credentials"

            env = {k: v for k, v in os.environ.items() if not k.startswith("BW_")
                   and k not in ("NODE_TLS_REJECT_UNAUTHORIZED", "NODE_OPTIONS")}
            env.update(BITWARDENCLI_APPDATA_DIR=str(work / "client-state"),
                       NODE_EXTRA_CA_CERTS=str(certificate), BW_NOINTERACTION="true",
                       BW_CLIENTID=first["client_id"], BW_CLIENTSECRET=first["client_secret"],
                       BW_PASSWORD=inputs["VAULTWARDEN_AUTOMATION_PASSWORD"])
            cli = str(work / "node_modules/.bin/bw")
            run([cli, "config", "server", server], env=env, step="configure HTTPS server")
            run([cli, "login", "--apikey"], env=env, step="API-key authentication")
            session = run([cli, "unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
                          env=env, step="unlock enrolled automation account")
            assert session, "unlock returned an empty session"
            env["BW_SESSION"] = session
            run([cli, "sync"], env=env, step="sync unlocked vault")
            status = json.loads(run([cli, "status"], env=env, step="verify unlocked status"))
            assert status["status"] == "unlocked", "session does not unlock a new CLI process"
            assert json.loads(run([cli, "list", "items"], env=env, step="read enrolled vault")) == []
            run([cli, "lock"], env=env, step="lock vault")
            env.pop("BW_SESSION")
            correct_password = env["BW_PASSWORD"]
            env["BW_PASSWORD"] = secrets.token_urlsafe(32)
            run([cli, "unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
                env=env, step="reject wrong master password", success=False)
            status = json.loads(run([cli, "status"], env=env, step="verify failed unlock state"))
            assert status["status"] == "locked", "wrong password unlocked vault"
            env["BW_PASSWORD"] = correct_password
            run([cli, "logout"], env=env, step="logout vault")
            env["BW_CLIENTSECRET"] = secrets.token_urlsafe(32)
            run([cli, "login", "--apikey"], env=env, step="reject wrong API key", success=False)
            print(f"CLI compatibility passed: {packages[0]}, Vaultwarden 1.37.3, verified HTTPS")
        finally:
            if proxy:
                proxy.shutdown()
                proxy.server_close()
            if container:
                run(["docker", "rm", "-f", container], step="remove owned fixture")


if __name__ == "__main__":
    check()
