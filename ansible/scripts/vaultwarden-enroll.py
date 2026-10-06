#!/usr/bin/env python3
"""Enroll the owner and automation accounts in Vaultwarden without a browser.

The web vault does its key work client-side, so neither `bw` nor plain HTTP can register
an account or create an organization. This script performs the same client-side steps the
web vault does, then drives the Vaultwarden API:

  1. register the owner and the automation account (both already invited by the admin
     facility, which is what lets them register while signups are closed) through the
     two-step send-verification-email / register/finish flow
  2. as the owner, create organization `homelab-infra`
  3. invite the automation account as Admin; with mail disabled Vaultwarden accepts an
     existing account's invitation immediately
  4. confirm it, handing it the organization key sealed to its public key
  5. read the automation account's personal API key

Every step reads state first, so a re-run converges instead of failing. Inputs come from
the environment only — never argv, which other processes can read:

  BW_SERVER                         https://vaultwarden.<domain>
  VAULTWARDEN_OWNER_EMAIL / _PASSWORD
  VAULTWARDEN_AUTOMATION_EMAIL / _PASSWORD

Output is one JSON object on stdout: {"client_id": ..., "client_secret": ...}.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

ORG_NAME = "homelab-infra"
KDF_ITERATIONS = 600_000  # Bitwarden's PBKDF2 default for new accounts
MEMBER_ADMIN = 1
STATUS_ACCEPTED, STATUS_CONFIRMED = 1, 2


def fail(message: str) -> None:
    print(f"vaultwarden-enroll: {message}", file=sys.stderr)
    sys.exit(1)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ── Bitwarden client-side crypto ─────────────────────────────────────────────────────


def master_key(password: str, email: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), email.strip().lower().encode(), KDF_ITERATIONS, 32)


def master_password_hash(mkey: bytes, password: str) -> str:
    return b64(hashlib.pbkdf2_hmac("sha256", mkey, password.encode(), 1, 32))


def stretch(mkey: bytes) -> bytes:
    enc = HKDFExpand(hashes.SHA256(), 32, b"enc").derive(mkey)
    mac = HKDFExpand(hashes.SHA256(), 32, b"mac").derive(mkey)
    return enc + mac


def enc_string(plain: bytes, key: bytes) -> str:
    """Type 2 EncString: AES-256-CBC with an HMAC-SHA256 over iv||ciphertext."""
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key[:32]), modes.CBC(iv)).encryptor()
    ct = encryptor.update(data) + encryptor.finalize()
    mac = hmac.new(key[32:], iv + ct, hashlib.sha256).digest()
    return f"2.{b64(iv)}|{b64(ct)}|{b64(mac)}"


def dec_string(value: str, key: bytes) -> bytes:
    kind, _, body = value.partition(".")
    if kind != "2":
        fail(f"unsupported symmetric EncString type {kind}")
    iv, ct, mac = (base64.b64decode(part) for part in body.split("|"))
    if not hmac.compare_digest(mac, hmac.new(key[32:], iv + ct, hashlib.sha256).digest()):
        fail("EncString MAC mismatch — wrong master password for an existing account")
    decryptor = Cipher(algorithms.AES(key[:32]), modes.CBC(iv)).decryptor()
    data = decryptor.update(ct) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(data) + unpadder.finalize()


OAEP = asym_padding.OAEP(mgf=asym_padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)


def rsa_seal(plain: bytes, public_key_b64: str) -> str:
    public = serialization.load_der_public_key(base64.b64decode(public_key_b64))
    return f"4.{b64(public.encrypt(plain, OAEP))}"


def rsa_open(value: str, private_der: bytes) -> bytes:
    kind, _, body = value.partition(".")
    if kind != "4":
        fail(f"unsupported asymmetric EncString type {kind}")
    private = serialization.load_der_private_key(private_der, password=None)
    return private.decrypt(base64.b64decode(body), OAEP)


def keypair(wrapping_key: bytes) -> tuple[str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    private_der = private.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return b64(public_der), enc_string(private_der, wrapping_key)


# ── HTTP ─────────────────────────────────────────────────────────────────────────────


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def request(method: str, url: str, *, token: str | None = None, json_body=None, form=None):
    headers = {"Accept": "application/json", "Device-Type": "8", "Bitwarden-Client-Name": "cli"}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as err:
        raise ApiError(err.code, err.read().decode(errors="replace")) from None
    return json.loads(raw) if raw.strip() else None


def field(obj: dict, name: str):
    """Vaultwarden answers camelCase; older builds answered PascalCase."""
    if name in obj:
        return obj[name]
    return obj.get(name[:1].upper() + name[1:])


# ── Account session ──────────────────────────────────────────────────────────────────


class Account:
    def __init__(self, server: str, email: str, password: str):
        self.server = server
        self.email = email.strip().lower()
        self.password = password
        self.mkey = master_key(password, self.email)
        self.hash = master_password_hash(self.mkey, password)
        self.token = ""
        self.user_key = b""
        self.private_der = b""

    def login(self) -> bool:
        try:
            resp = request(
                "POST",
                f"{self.server}/identity/connect/token",
                form={
                    "grant_type": "password",
                    "username": self.email,
                    "password": self.hash,
                    "scope": "api offline_access",
                    "client_id": "cli",
                    "device_type": "8",
                    "device_identifier": str(uuid.uuid5(uuid.NAMESPACE_URL, f"homelab-infra/{self.email}")),
                    "device_name": "homelab-infra enrollment",
                },
            )
        except ApiError as err:
            if err.status == 400:
                return False
            raise
        self.token = field(resp, "access_token")
        self.user_key = dec_string(field(resp, "key"), stretch(self.mkey))
        self.private_der = dec_string(field(resp, "privateKey"), self.user_key)
        return True

    def register(self) -> None:
        # Two-step registration. Verified live: 1.37.4 no longer routes the legacy
        # single-step POST /identity/accounts/register (404); 1.37.1 serves both. With
        # mail disabled, an admin-invited address gets its verification token back
        # directly; with mail enabled it is mailed instead (204), which this cannot use.
        token = request(
            "POST",
            f"{self.server}/identity/accounts/register/send-verification-email",
            json_body={"email": self.email, "name": self.email.split("@")[0][:50]},
        )
        if not isinstance(token, str) or not token:
            fail(
                f"Vaultwarden returned no registration token for {self.email}; enrollment "
                "needs mail disabled so the token is returned instead of mailed."
            )
        user_key = os.urandom(64)
        public, encrypted_private = keypair(user_key)
        request(
            "POST",
            f"{self.server}/identity/accounts/register/finish",
            json_body={
                "email": self.email,
                "emailVerificationToken": token,
                "masterPasswordHash": self.hash,
                "masterPasswordHint": None,
                "key": enc_string(user_key, stretch(self.mkey)),
                "kdf": 0,
                "kdfIterations": KDF_ITERATIONS,
                "keys": {"publicKey": public, "encryptedPrivateKey": encrypted_private},
            },
        )

    def ensure(self) -> None:
        if self.login():
            print(f"account {self.email}: already registered", file=sys.stderr)
            return
        try:
            self.register()
        except ApiError as err:
            fail(
                f"cannot register {self.email} ({err}). If the account already exists, its "
                "master password differs from the one in the seed file."
            )
        print(f"account {self.email}: registered", file=sys.stderr)
        if not self.login():
            fail(f"{self.email} registered but cannot log in")

    def api(self, method: str, path: str, body=None):
        return request(method, f"{self.server}/api{path}", token=self.token, json_body=body)


# ── Organization ─────────────────────────────────────────────────────────────────────


def ensure_organization(owner: Account) -> tuple[str, bytes]:
    profile = owner.api("GET", "/accounts/profile")
    for org in field(profile, "organizations") or []:
        if field(org, "name") == ORG_NAME:
            print(f"organization {ORG_NAME}: exists", file=sys.stderr)
            return field(org, "id"), rsa_open(field(org, "key"), owner.private_der)

    org_key = os.urandom(64)
    owner_public = b64(
        serialization.load_der_private_key(owner.private_der, password=None)
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    public, encrypted_private = keypair(org_key)
    org = owner.api(
        "POST",
        "/organizations",
        {
            "name": ORG_NAME,
            "billingEmail": owner.email,
            "collectionName": enc_string(b"Default Collection", org_key),
            "key": rsa_seal(org_key, owner_public),
            "keys": {"publicKey": public, "encryptedPrivateKey": encrypted_private},
            "planType": 0,
        },
    )
    print(f"organization {ORG_NAME}: created", file=sys.stderr)
    return field(org, "id"), org_key


def find_member(owner: Account, org_id: str, email: str) -> dict | None:
    users = owner.api("GET", f"/organizations/{org_id}/users")
    for member in field(users, "data") or []:
        if (field(member, "email") or "").lower() == email:
            return member
    return None


def ensure_admin_member(owner: Account, org_id: str, org_key: bytes, email: str) -> None:
    member = find_member(owner, org_id, email)
    if member is None:
        owner.api(
            "POST",
            f"/organizations/{org_id}/users/invite",
            {"emails": [email], "groups": [], "type": MEMBER_ADMIN, "collections": [], "permissions": {}},
        )
        print(f"member {email}: invited as Admin", file=sys.stderr)
        member = find_member(owner, org_id, email)
        if member is None:
            fail(f"{email} was invited but is not listed in {ORG_NAME}")

    if field(member, "type") != MEMBER_ADMIN:
        fail(f"{email} is a member of {ORG_NAME} but not an Admin; fix its role, then re-run")

    status = field(member, "status")
    if status == STATUS_CONFIRMED:
        print(f"member {email}: confirmed", file=sys.stderr)
        return
    if status != STATUS_ACCEPTED:
        fail(f"{email} membership is in state {status}, expected accepted (1); is SMTP enabled?")

    user_id = field(member, "userId")
    public = field(owner.api("GET", f"/users/{user_id}/public-key"), "publicKey")
    owner.api(
        "POST",
        f"/organizations/{org_id}/users/{field(member, 'id')}/confirm",
        {"key": rsa_seal(org_key, public)},
    )
    print(f"member {email}: confirmed", file=sys.stderr)


# ── Main ─────────────────────────────────────────────────────────────────────────────


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"{name} is required in the environment")
    return value


def main() -> None:
    server = env("BW_SERVER").rstrip("/")
    owner = Account(server, env("VAULTWARDEN_OWNER_EMAIL"), env("VAULTWARDEN_OWNER_PASSWORD"))
    automation = Account(server, env("VAULTWARDEN_AUTOMATION_EMAIL"), env("VAULTWARDEN_AUTOMATION_PASSWORD"))
    if owner.email == automation.email:
        fail("owner and automation accounts must be different")

    owner.ensure()
    automation.ensure()
    org_id, org_key = ensure_organization(owner)
    ensure_admin_member(owner, org_id, org_key, automation.email)

    profile = automation.api("GET", "/accounts/profile")
    api_key = automation.api("POST", "/accounts/api-key", {"masterPasswordHash": automation.hash})
    json.dump({"client_id": f"user.{field(profile, 'id')}", "client_secret": field(api_key, "apiKey")}, sys.stdout)


if __name__ == "__main__":
    main()
