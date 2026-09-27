#!/usr/bin/env python3
"""Crypto and resumability regression coverage for Vaultwarden enrollment."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.parse
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "ansible/scripts/vaultwarden-enroll.py"
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("vaultwarden_enroll", MODULE_PATH)
enroll = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = enroll
spec.loader.exec_module(enroll)


class FakeVaultwarden:
    """In-memory server for the exact endpoints and data contracts used by enrollment."""

    state: dict = {}

    def __init__(self, opener=None):
        del opener

    def request(self, method, url, *, headers=None, body=None, accepted=(200,)):
        headers = headers or {}
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path
        state = type(self).state
        payload = {}
        if body and headers.get("Content-Type", "").startswith("application/json"):
            payload = json.loads(body)

        def result(status=200, data=None):
            raw = json.dumps(data or {}).encode() if data is not None else b""
            if status not in accepted:
                raise enroll.APIError(method, path, status)
            return enroll.Response(status, raw)

        if path == "/api/accounts/prelogin" and method == "POST":
            return result(data={"kdfSettings": {"kdfType": 0, "iterations": 100_000}})

        if path == "/admin" and method == "POST":
            assert urllib.parse.parse_qs((body or b"").decode()) == {"token": ["admin-test-token"]}
            return result()
        if path == "/admin/invite" and method == "POST":
            email = payload["email"].lower()
            if email in state["invites"]:
                return result(409, {"error": "already invited"})
            state["invites"].add(email)
            state["writes"].append(("admin-invite", email))
            return result(data={"email": email})

        if path == "/identity/accounts/register" and method == "POST":
            email = payload["email"].lower()
            assert email in state["invites"]
            assert email not in state["accounts"]
            seed = dict(
                line.split("=", 1)
                for line in state["seed_path"].read_text().splitlines()
                if "=" in line
            )
            state["passwords"] = {
                "owner@example.net": seed["VAULTWARDEN_OWNER_MASTER_PASSWORD"],
                "automation@example.net": seed["VAULTWARDEN_AUTOMATION_MASTER_PASSWORD"],
            }
            account_id = str(uuid.uuid4())
            kdf = payload["masterPasswordAuthentication"]["kdf"]
            iteration_count = kdf["iterations"]
            password_key = state["passwords"][email]
            user_key = enroll.profile_user_key(
                {"key": payload["masterPasswordUnlock"]["key"]}, email, password_key, iteration_count
            )
            enc_private = enroll.cipherstring_decrypt(
                payload["keys"]["encryptedPrivateKey"], user_key[:32], user_key[32:]
            )
            private_key = serialization.load_der_private_key(enc_private, password=None)
            state["accounts"][email] = {
                "id": account_id,
                "email": email,
                "registration": payload,
                "private_key": private_key,
                "api_key": "",
            }
            state["writes"].append(("register", email))
            return result(data={"object": "register"})

        if path == "/identity/connect/token" and method == "POST":
            form = urllib.parse.parse_qs((body or b"").decode())
            if form.get("grant_type") == ["password"]:
                email = form.get("username", [""])[0].lower()
                account = state["accounts"].get(email)
                if not account or not hmac.compare_digest(
                    form.get("password", [""])[0],
                    account["registration"]["masterPasswordAuthentication"]["hash"],
                ):
                    return result(401, {"error": "invalid_grant"})
                token = "user-token-" + account["id"]
                state["tokens"][token] = email
                return result(data={"access_token": token, "token_type": "Bearer"})
            if form.get("grant_type") == ["client_credentials"]:
                client_id = form.get("client_id", [""])[0]
                email = next(
                    (item for item, value in state["accounts"].items()
                     if f"user.{value['id']}" == client_id
                     and value["api_key"] == form.get("client_secret", [""])[0]),
                    None,
                )
                if not email:
                    return result(401, {"error": "invalid_client"})
                token = "client-token-" + state["accounts"][email]["id"]
                state["tokens"][token] = email
                return result(data={"access_token": token, "token_type": "Bearer"})
            return result(400, {"error": "unsupported_grant_type"})

        if path == "/api/accounts/profile" and method == "GET":
            account = self._authorized(headers)
            memberships = []
            organization = state.get("organization")
            if organization and account["id"] == organization["owner_id"]:
                memberships.append({
                    "id": organization["id"],
                    "name": enroll.ORG_NAME,
                    "type": 0,
                    "status": 2,
                    "key": organization["owner_key"],
                })
            return result(data={
                "id": account["id"],
                "email": account["email"],
                "key": account["registration"]["masterPasswordUnlock"]["key"],
                "publicKey": account["registration"]["keys"]["publicKey"],
                "organizationsNew": memberships,
            })

        if path == "/api/organizations" and method == "POST":
            owner = self._authorized(headers)
            assert payload["name"] == enroll.ORG_NAME
            assert payload["collectionName"] == enroll.COLLECTION_NAME
            state["organization"] = {
                "id": str(uuid.uuid4()),
                "owner_id": owner["id"],
                "owner_key": payload["key"],
                "org_key": None,
                "members": [],
            }
            # Decrypt the owner's wrapped organization key using the same account key
            # recovered from the registration payload and its known Seed password.
            user_key = enroll.profile_user_key(
                {"key": owner["registration"]["masterPasswordUnlock"]["key"]},
                owner["email"], state["passwords"][owner["email"]], 100_000,
            )
            state["organization"]["org_key"] = enroll.cipherstring_decrypt(
                payload["key"], user_key[:32], user_key[32:]
            )
            state["writes"].append(("organization-create", enroll.ORG_NAME))
            return result(data={"id": state["organization"]["id"]})

        if path.startswith("/api/organizations/"):
            owner = self._authorized(headers)
            org = state["organization"]
            assert org and owner["id"] == org["owner_id"]
            suffix = path.split(f"/api/organizations/{org['id']}", 1)[-1]
            if suffix == "/users" and method == "GET":
                return result(data={"data": org["members"], "object": "list"})
            if suffix == "/users/invite" and method == "POST":
                assert payload["type"] == 1
                assert payload["collections"] == []
                automation = state["accounts"][payload["emails"][0].lower()]
                member = {
                    "id": str(uuid.uuid4()),
                    "userId": automation["id"],
                    "email": automation["email"],
                    "type": 1,
                    "status": 1,
                }
                org["members"].append(member)
                state["writes"].append(("organization-invite", automation["email"]))
                return result(data={"object": "list"})
            if suffix.startswith("/users/") and suffix.endswith("/confirm") and method == "POST":
                member_id = suffix.split("/users/", 1)[1].split("/confirm", 1)[0]
                member = next(item for item in org["members"] if item["id"] == member_id)
                automation = next(item for item in state["accounts"].values()
                                   if item["id"] == member["userId"])
                encrypted = base64.b64decode(payload["key"].removeprefix("4."))
                org_key = automation["private_key"].decrypt(
                    encrypted,
                    padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()),
                                 algorithm=hashes.SHA1(), label=None),
                )
                assert hmac.compare_digest(org_key, org["org_key"])
                member["key"] = payload["key"]
                member["status"] = 2
                state["writes"].append(("organization-confirm", member_id))
                return result(data={"object": "success"})

        if path.startswith("/api/users/") and path.endswith("/public-key") and method == "GET":
            user_id = path.split("/api/users/", 1)[1].split("/public-key", 1)[0]
            account = next(item for item in state["accounts"].values() if item["id"] == user_id)
            return result(data={"publicKey": account["registration"]["keys"]["publicKey"]})

        if path == "/api/accounts/api-key" and method == "POST":
            account = self._authorized(headers)
            assert payload.get("masterPasswordHash")
            if not account["api_key"]:
                account["api_key"] = "api-key-secret-for-" + account["id"]
                state["writes"].append(("api-key-create", account["email"]))
            return result(data={"apiKey": account["api_key"]})

        if path.startswith("/api/58/storage/keys/"):
            key_path = path.removeprefix("/api/58/storage/")
            if method == "GET":
                if key_path not in state["key_storage"]:
                    return result(404)
                return result(data={"path": key_path, "type": "password"})
            if method == "POST":
                assert key_path not in state["key_storage"]
                state["key_storage"][key_path] = (body or b"").decode()
                state["writes"].append(("key-storage", key_path))
                return result(201)

        raise AssertionError(f"unhandled fake request: {method} {path}")

    @classmethod
    def _authorized(cls, headers):
        token = headers.get("Authorization", "").removeprefix("Bearer ")
        email = cls.state["tokens"].get(token)
        assert email, "API request needs a valid bearer token"
        return cls.state["accounts"][email]


class EnrollmentCryptoAndFlowTest(unittest.TestCase):
    def test_bitwarden_crypto_vectors_and_wrapping(self):
        master_key = enroll.derive_master_key("Nobody@Example.com", "p4ssw0rd", 5000)
        self.assertEqual(
            enroll.derive_password_hash(master_key, "p4ssw0rd"),
            "r5CFRR+n9NQI8a525FY+0BPR0HGOjVJX0cR1KEMnIOo=",
        )
        self.assertEqual(
            enroll.derive_master_key("Nobody@Example.com", "p4ssw0rd", 5000),
            hashlib.pbkdf2_hmac("sha256", b"p4ssw0rd", b"nobody@example.com", 5000, 32),
        )

        encrypted = enroll.cipherstring_encrypt(b"wrapped-value", b"e" * 32, b"m" * 32)
        self.assertEqual(enroll.cipherstring_decrypt(encrypted, b"e" * 32, b"m" * 32), b"wrapped-value")
        altered = encrypted[:-1] + ("A" if encrypted[-1] != "A" else "B")
        with self.assertRaises(enroll.EnrollmentError):
            enroll.cipherstring_decrypt(altered, b"e" * 32, b"m" * 32)

        account = enroll.make_account_crypto("Nobody@Example.com", "p4ssw0rd", 100_000)
        self.assertEqual(account["registration"]["masterPasswordUnlock"]["salt"], "nobody@example.com")
        wrapped_user_key = account["registration"]["masterPasswordUnlock"]["key"]
        user_key = enroll.profile_user_key({"key": wrapped_user_key}, "Nobody@Example.com", "p4ssw0rd", 100_000)
        private_der = enroll.cipherstring_decrypt(
            account["registration"]["keys"]["encryptedPrivateKey"], user_key[:32], user_key[32:]
        )
        private_key = serialization.load_der_private_key(private_der, password=None)
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.assertEqual(base64.b64encode(public_der).decode(), account["public_key"])

        org_key = os.urandom(64)
        encrypted_org_key = enroll.organization_key_for_user(org_key, account["public_key"])
        decrypted_org_key = private_key.decrypt(
            base64.b64decode(encrypted_org_key.removeprefix("4.")),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()),
                         algorithm=hashes.SHA1(), label=None),
        )
        self.assertEqual(decrypted_org_key, org_key)

    def test_end_to_end_enrollment_is_seeded_and_converges_without_writes(self):
        with tempfile.TemporaryDirectory(prefix="vaultwarden-enrollment-") as tmp:
            seed_dir = Path(tmp)
            seed_path = seed_dir / "vaultwarden.env"
            seed_path.write_text("VAULTWARDEN_ADMIN_TOKEN=admin-test-token\n")
            seed_path.chmod(0o600)
            state = {
                "accounts": {},
                "invites": set(),
                "organization": None,
                "tokens": {},
                "key_storage": {},
                "writes": [],
                "seed_path": seed_path,
                "passwords": {},
            }
            FakeVaultwarden.state = state

            class TestHTTPClient(FakeVaultwarden, enroll.HTTPClient):
                pass

            enroll.HTTPClient = TestHTTPClient
            base_env = {
                "BW_SERVER": "https://vault.example",
                "VAULTWARDEN_OWNER_EMAIL": "owner@example.net",
                "VAULTWARDEN_AUTOMATION_EMAIL": "automation@example.net",
                "VAULTWARDEN_ADMIN_TOKEN": "admin-test-token",
                "RUNDECK_URL": "https://rundeck.example",
                "RUNDECK_PROJECT": "homelab-infra",
                "RUNDECK_API_TOKEN": "rundeck-test-token",
                "LAB_SECRETS_DIR": str(seed_dir),
            }
            first = enroll.run(base_env)
            self.assertTrue(first["changed"])
            self.assertTrue(first["automation_admin_confirmed"])
            self.assertEqual(len(state["accounts"]), 2)
            self.assertEqual(len(state["organization"]["members"]), 1)
            self.assertEqual(state["organization"]["members"][0]["status"], 2)
            self.assertEqual(len(state["key_storage"]), 3)
            self.assertTrue(all(path.startswith("keys/project/homelab-infra/vaultwarden-machine/")
                                for path in state["key_storage"]))

            seed = dict(line.split("=", 1) for line in seed_path.read_text().splitlines() if "=" in line)
            self.assertEqual(stat.S_IMODE(seed_path.stat().st_mode), 0o600)
            self.assertEqual(seed["VAULTWARDEN_ADMIN_TOKEN"], "admin-test-token")
            self.assertEqual(len(seed["VAULTWARDEN_OWNER_MASTER_PASSWORD"]), 64)
            self.assertEqual(len(seed["VAULTWARDEN_AUTOMATION_MASTER_PASSWORD"]), 64)
            self.assertNotEqual(seed["VAULTWARDEN_OWNER_MASTER_PASSWORD"],
                                seed["VAULTWARDEN_AUTOMATION_MASTER_PASSWORD"])
            # The fake server read the generated values only to verify client-side wrapping.
            for email, password in state["passwords"].items():
                self.assertEqual(len(password), 64, email)
            self.assertNotIn(seed["VAULTWARDEN_OWNER_MASTER_PASSWORD"], json.dumps(first))
            self.assertNotIn(seed["VAULTWARDEN_AUTOMATION_MASTER_PASSWORD"], json.dumps(first))

            second_env = base_env | {
                "BW_CLIENTID": state["key_storage"][
                    "keys/project/homelab-infra/vaultwarden-machine/client-id"
                ],
                "BW_CLIENTSECRET": state["key_storage"][
                    "keys/project/homelab-infra/vaultwarden-machine/client-secret"
                ],
                "BW_PASSWORD": state["key_storage"][
                    "keys/project/homelab-infra/vaultwarden-machine/master-password"
                ],
            }
            write_count = len(state["writes"])
            second = enroll.run(second_env)
            self.assertFalse(second["changed"])
            self.assertEqual(len(state["writes"]), write_count)

            conflict_env = second_env | {"BW_CLIENTSECRET": "conflicting-value"}
            with self.assertRaises(enroll.EnrollmentError):
                enroll.run(conflict_env)
            self.assertEqual(len(state["writes"]), write_count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
