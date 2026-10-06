#!/usr/bin/env python3
"""Drive scripts/vaultwarden-enroll.py against an in-process fake Vaultwarden.

The fake keeps the server-side state the real one does (users, keys, organization,
memberships) and enforces the same preconditions: registration only for an invited
address, login only with the registered hash, confirmation only from Accepted. The test
then proves what matters end to end rather than trusting the script's own report: the key
the automation account was confirmed with really opens, with that account's private key
unwrapped from its password, to the organization key the owner holds. A second run must
change nothing, and a wrong password must fail instead of re-registering.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import threading
import unittest
import urllib.parse
import uuid
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vw_enroll", ROOT / "ansible/scripts/vaultwarden-enroll.py")
vw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vw)

OWNER, AUTOMATION = "owner@example.com", "homelab-infra@example.com"


class FakeVaultwarden:
    def __init__(self, invited: list[str]):
        self.users: dict[str, dict] = {}  # email -> user
        self.invited = set(invited)
        self.tokens: dict[str, str] = {}  # bearer -> email
        self.orgs: dict[str, dict] = {}
        self.members: dict[str, dict] = {}  # member id -> membership
        self.writes = 0

    def user_by_id(self, user_id: str) -> dict:
        return next(u for u in self.users.values() if u["id"] == user_id)

    def handle(self, method: str, path: str, bearer: str | None, body):
        me = self.users.get(self.tokens.get(bearer or "", ""))
        if (method, path) == ("POST", "/identity/connect/token"):
            user = self.users.get(body["username"])
            if not user or user["hash"] != body["password"]:
                return 400, {"error": "invalid_grant"}
            token = uuid.uuid4().hex
            self.tokens[token] = user["email"]
            return 200, {"access_token": token, "Key": user["key"], "PrivateKey": user["private"]}
        # Only the two-step flow is routed, as on 1.37.4; the legacy single-step
        # /identity/accounts/register falls through to the unauthenticated refusal below.
        if (method, path) == ("POST", "/identity/accounts/register/send-verification-email"):
            # Mail is disabled, so an invited address gets its token back directly.
            if body["email"] not in self.invited:
                return 400, {"message": "Registration not allowed or user already exists"}
            return 200, "verify:" + body["email"]
        if (method, path) == ("POST", "/identity/accounts/register/finish"):
            email = body["email"]
            if body.get("emailVerificationToken") != "verify:" + email:
                return 400, {"message": "Email verification token does not match email"}
            if email in self.users or email not in self.invited:
                return 400, {"message": "Registration not allowed or user already exists"}
            self.writes += 1
            self.users[email] = {
                "id": str(uuid.uuid4()),
                "email": email,
                "hash": body["masterPasswordHash"],
                "key": body["key"],
                "public": body["keys"]["publicKey"],
                "private": body["keys"]["encryptedPrivateKey"],
                "api_key": uuid.uuid4().hex,
            }
            return 200, {}
        if me is None:
            return 401, {"message": "unauthorized"}
        if (method, path) == ("GET", "/api/accounts/profile"):
            orgs = [
                {"id": m["org"], "name": self.orgs[m["org"]]["name"], "key": m["akey"]}
                for m in self.members.values()
                if m["user"] == me["id"] and m["status"] == 2
            ]
            return 200, {"id": me["id"], "organizations": orgs}
        if (method, path) == ("POST", "/api/organizations"):
            self.writes += 1
            org_id = str(uuid.uuid4())
            self.orgs[org_id] = {"name": body["name"], "keys": body["keys"]}
            self.members[str(uuid.uuid4())] = {
                "org": org_id, "user": me["id"], "status": 2, "type": 0, "akey": body["key"],
            }
            return 200, {"id": org_id}
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "organizations"] and parts[3:] == ["users"] and method == "GET":
            data = [
                {"id": mid, "userId": m["user"], "email": self.user_by_id(m["user"])["email"],
                 "status": m["status"], "type": m["type"]}
                for mid, m in self.members.items() if m["org"] == parts[2]
            ]
            return 200, {"data": data}
        if parts[:2] == ["api", "organizations"] and parts[3:] == ["users", "invite"]:
            self.writes += 1
            for email in body["emails"]:
                # Mail is disabled and the account exists, so the invite is accepted at once.
                self.members[str(uuid.uuid4())] = {
                    "org": parts[2], "user": self.users[email]["id"], "status": 1,
                    "type": int(body["type"]), "akey": "",
                }
            return 200, {}
        if parts[:2] == ["api", "organizations"] and parts[5:] == ["confirm"]:
            member = self.members[parts[4]]
            if member["status"] != 1:
                return 400, {"message": "User in invalid state"}
            self.writes += 1
            member.update(status=2, akey=body["key"])
            return 200, {}
        if parts[:2] == ["api", "users"] and parts[3:] == ["public-key"]:
            return 200, {"publicKey": self.user_by_id(parts[2])["public"]}
        if (method, path) == ("POST", "/api/accounts/api-key"):
            if body["masterPasswordHash"] != me["hash"]:
                return 400, {"message": "Invalid password"}
            return 200, {"apiKey": me["api_key"]}
        return 404, {"message": f"no route {method} {path}"}


def serve(fake: FakeVaultwarden) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def dispatch(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode() if length else ""
            if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                body = dict(urllib.parse.parse_qsl(raw))
            elif self.headers.get("Content-Type") == "application/x-rundeck-data-password":
                body = raw
            else:
                body = json.loads(raw) if raw else None
            auth = self.headers.get("Authorization", "")
            status, payload = fake.handle(self.command, self.path, auth.removeprefix("Bearer ") or None, body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = dispatch

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class EnrollTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeVaultwarden([OWNER, AUTOMATION])
        self.server = serve(self.fake)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        vw.KDF_ITERATIONS = 1000  # the real 600k only slows the test; the math is identical
        self.env = {
            "BW_SERVER": f"http://127.0.0.1:{self.server.server_address[1]}",
            "VAULTWARDEN_OWNER_EMAIL": OWNER,
            "VAULTWARDEN_OWNER_PASSWORD": "owner-password-0123456789",
            "VAULTWARDEN_AUTOMATION_EMAIL": AUTOMATION,
            "VAULTWARDEN_AUTOMATION_PASSWORD": "automation-password-0123456789",
        }

    def run_enroll(self, **overrides) -> tuple[dict, str]:
        out, err = io.StringIO(), io.StringIO()
        old = {k: os.environ.get(k) for k in self.env}
        os.environ.update({**self.env, **overrides})
        try:
            with redirect_stdout(out), redirect_stderr(err):
                vw.main()
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return json.loads(out.getvalue()), err.getvalue()

    def unwrap(self, email: str, password: str) -> tuple[bytes, bytes]:
        user = self.fake.users[email]
        user_key = vw.dec_string(user["key"], vw.stretch(vw.master_key(password, email)))
        return user_key, vw.dec_string(user["private"], user_key)

    def test_full_ceremony_and_key_agreement(self):
        result, log = self.run_enroll()
        automation = self.fake.users[AUTOMATION]
        self.assertEqual(result["client_id"], f"user.{automation['id']}")
        self.assertEqual(result["client_secret"], automation["api_key"])
        self.assertIn("organization homelab-infra: created", log)

        members = {self.fake.user_by_id(m["user"])["email"]: m for m in self.fake.members.values()}
        self.assertEqual(members[AUTOMATION]["status"], 2)
        self.assertEqual(members[AUTOMATION]["type"], vw.MEMBER_ADMIN)

        _, owner_private = self.unwrap(OWNER, self.env["VAULTWARDEN_OWNER_PASSWORD"])
        _, automation_private = self.unwrap(AUTOMATION, self.env["VAULTWARDEN_AUTOMATION_PASSWORD"])
        owner_org_key = vw.rsa_open(members[OWNER]["akey"], owner_private)
        automation_org_key = vw.rsa_open(members[AUTOMATION]["akey"], automation_private)
        self.assertEqual(len(owner_org_key), 64)
        self.assertEqual(owner_org_key, automation_org_key)

        org = next(iter(self.fake.orgs.values()))
        org_private = vw.dec_string(org["keys"]["encryptedPrivateKey"], owner_org_key)
        self.assertTrue(vw.rsa_open(vw.rsa_seal(b"probe", org["keys"]["publicKey"]), org_private) == b"probe")

    def test_rerun_converges_without_writes(self):
        first, _ = self.run_enroll()
        writes = self.fake.writes
        second, log = self.run_enroll()
        self.assertEqual(first, second)
        self.assertEqual(self.fake.writes, writes)
        self.assertIn(f"account {OWNER}: already registered", log)
        self.assertIn("organization homelab-infra: exists", log)

    def test_wrong_password_for_existing_account_fails(self):
        self.run_enroll()
        with self.assertRaises(SystemExit):
            self.run_enroll(VAULTWARDEN_OWNER_PASSWORD="not-the-registered-password")

    def test_uninvited_account_cannot_register(self):
        self.fake.invited.discard(AUTOMATION)
        with self.assertRaises(SystemExit):
            self.run_enroll()


if __name__ == "__main__":
    unittest.main()
