#!/usr/bin/env python3
"""Unattended, resumable Vaultwarden account and organization enrollment.

The registration and organization endpoints speak Bitwarden's client-side
encryption format. Passwords and encryption keys are derived and encrypted on
the runner; only the derived registration data crosses the HTTPS API. Generated
master passwords stay in the runner's Seed file until the verified cutover.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import secrets
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.primitives import hashes, padding, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError as error:  # pragma: no cover - exercised by runtime dependency checks
    raise SystemExit("Vaultwarden enrollment requires the runner's cryptography package") from error


ORG_NAME = "homelab-infra"
COLLECTION_NAME = "Default Collection"
API_VERSION = 58
KDF_PBKDF2_SHA256 = 0
ACCOUNT_PASSWORD_KEYS = (
    "VAULTWARDEN_OWNER_MASTER_PASSWORD",
    "VAULTWARDEN_AUTOMATION_MASTER_PASSWORD",
)


class EnrollmentError(RuntimeError):
    """An actionable enrollment failure without secret-bearing response bodies."""


class APIError(EnrollmentError):
    def __init__(self, method: str, path: str, status: int):
        super().__init__(f"{method} {path} returned HTTP {status}")
        self.status = status


@dataclass
class Response:
    status: int
    body: bytes


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def hkdf_expand(key: bytes, info: bytes, length: int = 32) -> bytes:
    """Bitwarden's HKDF-Expand (no extract step, as the master key is PRK)."""
    output = b""
    previous = b""
    counter = 1
    while len(output) < length:
        previous = hmac.new(key, previous + info + bytes([counter]), hashlib.sha256).digest()
        output += previous
        counter += 1
    return output[:length]


def derive_master_key(email: str, password: str, iterations: int) -> bytes:
    if iterations < 1:
        raise EnrollmentError("Vaultwarden requested invalid PBKDF2 parameters")
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), email.strip().lower().encode("utf-8"), iterations, 32
    )


def derive_password_hash(master_key: bytes, password: str) -> str:
    return b64(hashlib.pbkdf2_hmac("sha256", master_key, password.encode("utf-8"), 1, 32))


def stretched_keys(master_key: bytes) -> tuple[bytes, bytes]:
    return hkdf_expand(master_key, b"enc"), hkdf_expand(master_key, b"mac")


def cipherstring_encrypt(plaintext: bytes, encryption_key: bytes, mac_key: bytes) -> str:
    iv = secrets.token_bytes(16)
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    mac = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
    return f"2.{b64(iv)}|{b64(ciphertext)}|{b64(mac)}"


def cipherstring_decrypt(value: str, encryption_key: bytes, mac_key: bytes) -> bytes:
    try:
        version, data = value.split(".", 1)
        if version != "2":
            raise ValueError("unexpected cipher type")
        iv_text, ciphertext_text, mac_text = data.split("|")
        iv, ciphertext, actual_mac = map(b64decode, (iv_text, ciphertext_text, mac_text))
        expected_mac = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(actual_mac, expected_mac):
            raise ValueError("cipher authentication failed")
        decryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except (ValueError, TypeError) as error:
        raise EnrollmentError("Vaultwarden returned an invalid encrypted key") from error


def make_account_crypto(email: str, password: str, iterations: int) -> dict[str, Any]:
    master_key = derive_master_key(email, password, iterations)
    enc_key, mac_key = stretched_keys(master_key)
    user_key = secrets.token_bytes(64)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_der = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    kdf = {
        "kdfType": KDF_PBKDF2_SHA256,
        "iterations": iterations,
        "memory": None,
        "parallelism": None,
    }
    normalized_email = email.strip().lower()
    return {
        "master_key": master_key,
        "user_key": user_key,
        "public_key": b64(public_der),
        "private_key": private_der,
        "kdf": kdf,
        "registration": {
            "email": email,
            "name": "homelab-infra",
            "masterPasswordAuthentication": {
                "kdf": kdf,
                "salt": normalized_email,
                "hash": derive_password_hash(master_key, password),
            },
            "masterPasswordUnlock": {
                "kdf": kdf,
                "salt": normalized_email,
                "key": cipherstring_encrypt(user_key, enc_key, mac_key),
            },
            "keys": {
                "encryptedPrivateKey": cipherstring_encrypt(private_der, user_key[:32], user_key[32:]),
                "publicKey": b64(public_der),
            },
        },
    }


def profile_user_key(profile: dict[str, Any], email: str, password: str, iterations: int) -> bytes:
    master_key = derive_master_key(email, password, iterations)
    enc_key, mac_key = stretched_keys(master_key)
    key = profile.get("key")
    if not isinstance(key, str) or not key:
        raise EnrollmentError("Vaultwarden account has no wrapped user key")
    user_key = cipherstring_decrypt(key, enc_key, mac_key)
    if len(user_key) != 64:
        raise EnrollmentError("Vaultwarden account key has an unexpected size")
    return user_key


def organization_key_for_user(org_key: bytes, public_key: str) -> str:
    try:
        der = b64decode(public_key)
        key = serialization.load_der_public_key(der)
        encrypted = key.encrypt(
            org_key,
            asymmetric_padding.OAEP(
                mgf=asymmetric_padding.MGF1(algorithm=hashes.SHA1()),
                algorithm=hashes.SHA1(),
                label=None,
            ),
        )
        return "4." + b64(encrypted)
    except (ValueError, TypeError) as error:
        raise EnrollmentError("The automation account has an unsupported public key") from error


class HTTPClient:
    def __init__(self, opener: urllib.request.OpenerDirector | None = None):
        self.opener = opener or urllib.request.build_opener()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        accepted: tuple[int, ...] = (200,),
    ) -> Response:
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                result = Response(response.status, response.read())
        except urllib.error.HTTPError as error:
            result = Response(error.code, error.read())
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # Never serialize an exception that can contain a URL or request details.
            raise EnrollmentError(f"Could not reach {method} {urllib.parse.urlsplit(url).path}") from error
        if result.status not in accepted:
            raise APIError(method, urllib.parse.urlsplit(url).path, result.status)
        return result

    def json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        accepted: tuple[int, ...] = (200,),
    ) -> Any:
        merged = {"Accept": "application/json"}
        if headers:
            merged.update(headers)
        body = None
        if data is not None:
            merged["Content-Type"] = "application/json"
            body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        response = self.request(method, url, headers=merged, body=body, accepted=accepted)
        if not response.body:
            return None
        try:
            return json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EnrollmentError(f"{method} {urllib.parse.urlsplit(url).path} returned invalid JSON") from error


def kdf_settings(server: HTTPClient, base_url: str, email: str) -> tuple[int, dict[str, Any]]:
    result = server.json("POST", f"{base_url}/api/accounts/prelogin", data={"email": email})
    settings = result.get("kdfSettings") if isinstance(result, dict) else None
    if not isinstance(settings, dict):
        settings = {
            "kdfType": result.get("kdf"),
            "iterations": result.get("kdfIterations"),
            "memory": result.get("kdfMemory"),
            "parallelism": result.get("kdfParallelism"),
        }
    if settings.get("kdfType") != KDF_PBKDF2_SHA256:
        raise EnrollmentError("This enrollment flow supports Vaultwarden PBKDF2-SHA256 accounts only")
    iterations = settings.get("iterations")
    if not isinstance(iterations, int) or iterations < 100_000:
        raise EnrollmentError("Vaultwarden returned invalid PBKDF2 parameters")
    return iterations, {
        "kdfType": KDF_PBKDF2_SHA256,
        "iterations": iterations,
        "memory": None,
        "parallelism": None,
    }


def account_login(server: HTTPClient, base_url: str, email: str, password: str, iterations: int) -> str:
    master_key = derive_master_key(email, password, iterations)
    password_hash = derive_password_hash(master_key, password)
    data = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "username": email,
            "password": password_hash,
            "scope": "api offline_access",
            "client_id": "web",
            "deviceType": "9",
            "deviceIdentifier": str(uuid.uuid4()),
            "deviceName": "homelab-infra enrollment",
        }
    ).encode("utf-8")
    # Form requests are kept out of HTTPClient.json so the password-derived hash is never
    # accidentally treated as JSON or included in an exception message.
    raw = server.request(
        "POST",
        f"{base_url}/identity/connect/token",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        body=data,
        accepted=(200, 400, 401),
    )
    if raw.status in (400, 401):
        raise APIError("POST", "/identity/connect/token", raw.status)
    try:
        token = json.loads(raw.body).get("access_token", "")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        token = ""
    if not token:
        raise EnrollmentError("Vaultwarden login did not return an access token")
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def admin_invite(base_url: str, admin_token: str, identities: list[str]) -> None:
    cookies = http.cookiejar.CookieJar()
    admin_http = HTTPClient(urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies)))
    form = urllib.parse.urlencode({"token": admin_token}).encode("utf-8")
    admin_http.request(
        "POST",
        f"{base_url}/admin",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=form,
        accepted=(200, 303),
    )
    for email in identities:
        admin_http.json(
            "POST",
            f"{base_url}/admin/invite",
            data={"email": email},
            accepted=(200, 409),
        )


def load_seed(path: Path) -> tuple[dict[str, str], bool]:
    values: dict[str, str] = {}
    lines: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise EnrollmentError("Could not read the Vaultwarden Seed file") from error
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    changed = False
    for key in ACCOUNT_PASSWORD_KEYS:
        if not values.get(key):
            # URL-safe output has no whitespace or shell metacharacters and is a 384-bit
            # random secret. It remains hidden in Ansible output and never enters argv.
            values[key] = secrets.token_urlsafe(48)
            changed = True
    if changed or not path.exists() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        content = "\n".join(
            line for line in lines if line and ("=" not in line or line.lstrip().startswith("#"))
        )
        assignments = [f"{key}={value}" for key, value in values.items()]
        if content:
            content += "\n"
        content += "\n".join(assignments) + "\n"
        path.parent.chmod(0o700)
        fd, temp_name = tempfile.mkstemp(prefix=".vaultwarden-seed.", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return values, True
    return values, False


def ensure_account(
    server: HTTPClient, base_url: str, email: str, password: str
) -> tuple[dict[str, Any], str, int, bool]:
    iterations, _ = kdf_settings(server, base_url, email)
    changed = False
    try:
        token = account_login(server, base_url, email, password, iterations)
    except APIError as error:
        if error.status not in (400, 401):
            raise
        crypto = make_account_crypto(email, password, iterations)
        server.json(
            "POST",
            f"{base_url}/identity/accounts/register",
            data=crypto["registration"],
            accepted=(200,),
        )
        changed = True
        token = account_login(server, base_url, email, password, iterations)
    profile = server.json("GET", f"{base_url}/api/accounts/profile", headers=bearer(token))
    # Read the encrypted key back and authenticate it locally on both a first registration
    # and a retry. This catches a bad crypto payload before org creation or Key Storage writes.
    profile_user_key(profile, email, password, iterations)
    return profile, token, iterations, changed


def resolve_org(
    server: HTTPClient,
    base_url: str,
    profile: dict[str, Any],
    owner_email: str,
    owner_password: str,
    owner_token: str,
    owner_iterations: int,
) -> tuple[str, bytes, bool]:
    user_key = profile_user_key(profile, owner_email, owner_password, owner_iterations)
    memberships = profile.get("organizationsNew") or profile.get("organizations") or []
    matching = [
        item
        for item in memberships
        if item.get("name") == ORG_NAME and str(item.get("type")) in {"0", "Owner"}
    ]
    if matching:
        member = matching[0]
        try:
            org_key = cipherstring_decrypt(member["key"], user_key[:32], user_key[32:])
        except (KeyError, EnrollmentError) as error:
            raise EnrollmentError("The existing homelab-infra organization key cannot be recovered") from error
        if len(org_key) != 64:
            raise EnrollmentError("The existing homelab-infra organization key has an unexpected size")
        return member["id"], org_key, False

    org_key = secrets.token_bytes(64)
    org_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_der = org_private.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_der = org_private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    result = server.json(
        "POST",
        f"{base_url}/api/organizations",
        headers=bearer(owner_token),
        data={
            "name": ORG_NAME,
            "billingEmail": owner_email,
            "collectionName": COLLECTION_NAME,
            "planType": 6,
            "key": cipherstring_encrypt(org_key, user_key[:32], user_key[32:]),
            "keys": {
                "encryptedPrivateKey": cipherstring_encrypt(private_der, org_key[:32], org_key[32:]),
                "publicKey": b64(public_der),
            },
        },
        accepted=(200,),
    )
    org_id = result.get("id") if isinstance(result, dict) else None
    if not org_id:
        raise EnrollmentError("Vaultwarden did not return the created organization ID")
    return str(org_id), org_key, True


def ensure_admin_membership(
    server: HTTPClient,
    base_url: str,
    org_id: str,
    owner_token: str,
    automation_email: str,
    automation_user_id: str,
    org_key: bytes,
) -> bool:
    headers = bearer(owner_token)
    members_url = f"{base_url}/api/organizations/{urllib.parse.quote(org_id, safe='')}/users"
    members = server.json("GET", members_url, headers=headers)
    data = members.get("data", []) if isinstance(members, dict) else []
    matching = [member for member in data if member.get("email", "").lower() == automation_email.lower()]
    changed = False
    if not matching:
        server.json(
            "POST",
            f"{members_url.rsplit('/users', 1)[0]}/users/invite",
            headers=headers,
            data={"emails": [automation_email], "type": 1, "collections": [], "groups": []},
            accepted=(200,),
        )
        changed = True
        members = server.json("GET", members_url, headers=headers)
        data = members.get("data", []) if isinstance(members, dict) else []
        matching = [member for member in data if member.get("email", "").lower() == automation_email.lower()]
    if len(matching) != 1:
        raise EnrollmentError("Vaultwarden did not return exactly one automation organization member")
    member = matching[0]
    member_id = member.get("id")
    member_type = member.get("type")
    status = member.get("status")
    if str(member_type) not in {"1", "Admin"}:
        raise EnrollmentError("The automation account is not an Admin of homelab-infra")
    if status == 2:
        return changed
    if status != 1 or not member_id:
        raise EnrollmentError(
            "The automation invitation is not accepted; Vaultwarden must have mail disabled for unattended enrollment"
        )
    public_key = server.json(
        "GET",
        f"{base_url}/api/users/{urllib.parse.quote(automation_user_id, safe='')}/public-key",
        headers=headers,
    ).get("publicKey", "")
    encrypted_key = organization_key_for_user(org_key, public_key)
    server.json(
        "POST",
        f"{members_url}/{urllib.parse.quote(str(member_id), safe='')}/confirm",
        headers=headers,
        data={"key": encrypted_key},
        accepted=(200,),
    )
    return True


def key_storage_request(
    http: HTTPClient,
    method: str,
    base_url: str,
    path: str,
    token: str,
    *,
    content: str | None = None,
    accepted: tuple[int, ...],
) -> Response:
    encoded_path = urllib.parse.quote(path, safe="/")
    url = f"{base_url}/api/{API_VERSION}/storage/{encoded_path}"
    headers = {"X-Rundeck-Auth-Token": token, "Accept": "application/json"}
    body = None
    if content is not None:
        headers["Content-Type"] = "application/x-rundeck-data-password"
        body = content.encode("utf-8")
    return http.request(method, url, headers=headers, body=body, accepted=accepted)


def stage_secret(
    http: HTTPClient,
    url: str,
    token: str,
    path: str,
    content: str,
    supplied_option: str,
) -> bool:
    metadata = key_storage_request(http, "GET", url, path, token, accepted=(200, 404))
    if metadata.status == 200:
        if not supplied_option:
            raise EnrollmentError(f"Key Storage path already exists but its secure option was not supplied: {path}")
        if not hmac.compare_digest(supplied_option, content):
            raise EnrollmentError(f"Existing Key Storage value conflicts with enrollment: {path}")
        return False
    if supplied_option:
        raise EnrollmentError(f"Rundeck supplied a Key Storage value for a missing path: {path}")
    result = key_storage_request(
        http, "POST", url, path, token, content=content, accepted=(200, 201, 409)
    )
    if result.status == 409:
        # Another enrollment finished the create between the metadata lookup and POST.
        # Do not replace it; next execution can load and compare the secure job option.
        raise EnrollmentError(f"Key Storage path was concurrently created; retry enrollment: {path}")
    return True


def verify_api_key(server: HTTPClient, base_url: str, client_id: str, api_key: str) -> None:
    form = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": "api", "client_id": client_id, "client_secret": api_key}
    ).encode("utf-8")
    login = server.request(
        "POST",
        f"{base_url}/identity/connect/token",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        body=form,
        accepted=(200, 400, 401),
    )
    try:
        token = json.loads(login.body).get("access_token", "") if login.status == 200 else ""
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        token = ""
    if not token:
        raise EnrollmentError("The Vaultwarden automation API key could not authenticate")


def generate_or_reuse_api_key(
    server: HTTPClient,
    base_url: str,
    automation_profile: dict[str, Any],
    automation_token: str,
    email: str,
    password: str,
    iterations: int,
    existing_client_id: str,
    existing_client_secret: str,
    existing_master_password: str,
) -> tuple[str, str, bool]:
    expected_client_id = f"user.{automation_profile['id']}"
    if (
        existing_client_id
        and existing_client_secret
        and existing_master_password
        and hmac.compare_digest(existing_client_id, expected_client_id)
        and hmac.compare_digest(existing_master_password, password)
    ):
        verify_api_key(server, base_url, expected_client_id, existing_client_secret)
        return expected_client_id, existing_client_secret, False
    password_hash = derive_password_hash(derive_master_key(email, password, iterations), password)
    result = server.json(
        "POST",
        f"{base_url}/api/accounts/api-key",
        headers=bearer(automation_token),
        data={"masterPasswordHash": password_hash},
    )
    api_key = result.get("apiKey", "") if isinstance(result, dict) else ""
    if not api_key:
        raise EnrollmentError("Vaultwarden did not return the automation API key")
    # Verify before persisting; also detects an externally changed Key Storage value on
    # otherwise converged runs without ever replacing it.
    verify_api_key(server, base_url, expected_client_id, api_key)
    return expected_client_id, api_key, True


def validate_inputs(environ: dict[str, str]) -> tuple[str, str, str, str]:
    base_url = environ.get("BW_SERVER", "").rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    allow_http = environ.get("HOMELABINFRA_ENROLL_TEST_ALLOW_HTTP") == "1"
    if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
        raise EnrollmentError("BW_SERVER must be an HTTPS Vaultwarden URL")
    if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise EnrollmentError("BW_SERVER is not a valid base URL")
    owner_email = environ.get("VAULTWARDEN_OWNER_EMAIL", "").strip()
    automation_email = environ.get("VAULTWARDEN_AUTOMATION_EMAIL", "").strip()
    if not owner_email or "@" not in owner_email or " " in owner_email:
        raise EnrollmentError("VAULTWARDEN_OWNER_EMAIL is required")
    if not automation_email or "@" not in automation_email or " " in automation_email:
        raise EnrollmentError("VAULTWARDEN_AUTOMATION_EMAIL is invalid")
    if owner_email.lower() == automation_email.lower():
        raise EnrollmentError("Owner and automation account emails must be distinct")
    admin_token = environ.get("VAULTWARDEN_ADMIN_TOKEN", "")
    if not admin_token:
        raise EnrollmentError("VAULTWARDEN_ADMIN_TOKEN is required")
    return base_url, owner_email, automation_email, admin_token


def run(environ: dict[str, str]) -> dict[str, Any]:
    base_url, owner_email, automation_email, admin_token = validate_inputs(environ)
    secrets_dir = Path(environ.get("LAB_SECRETS_DIR", "/etc/homelab-infra/secrets.d"))
    seed_file = secrets_dir / "vaultwarden.env"
    seed, seed_changed = load_seed(seed_file)
    owner_password = seed[ACCOUNT_PASSWORD_KEYS[0]]
    automation_password = seed[ACCOUNT_PASSWORD_KEYS[1]]
    changed = seed_changed

    # Admin invitations are the supported registration gate while public signup is off.
    admin_invite(base_url, admin_token, [owner_email, automation_email])
    server = HTTPClient()
    owner_profile, owner_token, owner_iterations, account_created = ensure_account(
        server, base_url, owner_email, owner_password
    )
    changed |= account_created
    automation_profile, automation_token, automation_iterations, automation_created = ensure_account(
        server, base_url, automation_email, automation_password
    )
    changed |= automation_created

    org_id, org_key, org_created = resolve_org(
        server,
        base_url,
        owner_profile,
        owner_email,
        owner_password,
        owner_token,
        owner_iterations,
    )
    changed |= org_created
    member_changed = ensure_admin_membership(
        server,
        base_url,
        org_id,
        owner_token,
        automation_email,
        str(automation_profile.get("id", "")),
        org_key,
    )
    changed |= member_changed

    rundeck_url = environ.get("RUNDECK_URL", "").rstrip("/")
    rundeck_project = environ.get("RUNDECK_PROJECT", "")
    rundeck_token = environ.get("RUNDECK_API_TOKEN", "")
    if not rundeck_url or not rundeck_project or not rundeck_token:
        raise EnrollmentError("Enrollment needs RUNDECK_URL, RUNDECK_PROJECT and the Rundeck API token")
    existing_id = environ.get("BW_CLIENTID", "") or environ.get("RD_OPTION_BW_CLIENTID", "")
    existing_secret = environ.get("BW_CLIENTSECRET", "") or environ.get("RD_OPTION_BW_CLIENTSECRET", "")
    existing_password = environ.get("BW_PASSWORD", "") or environ.get("RD_OPTION_BW_PASSWORD", "")
    client_id, client_secret, api_key_ready = generate_or_reuse_api_key(
        server,
        base_url,
        automation_profile,
        automation_token,
        automation_email,
        automation_password,
        automation_iterations,
        existing_id,
        existing_secret,
        existing_password,
    )
    changed |= api_key_ready

    rundeck = HTTPClient()
    path_base = f"keys/project/{rundeck_project}/vaultwarden-machine"
    changed |= stage_secret(
        rundeck,
        rundeck_url,
        rundeck_token,
        f"{path_base}/client-id",
        client_id,
        existing_id,
    )
    changed |= stage_secret(
        rundeck,
        rundeck_url,
        rundeck_token,
        f"{path_base}/client-secret",
        client_secret,
        existing_secret,
    )
    changed |= stage_secret(
        rundeck,
        rundeck_url,
        rundeck_token,
        f"{path_base}/master-password",
        automation_password,
        existing_password,
    )
    return {
        "changed": changed,
        "owner_registered": True,
        "automation_registered": True,
        "organization": ORG_NAME,
        "automation_admin_confirmed": True,
        "key_storage_staged": True,
    }


def main() -> int:
    try:
        result = run(dict(os.environ))
    except (EnrollmentError, OSError) as error:
        # Exception strings are deliberately built only from operation names/statuses.
        # The Ansible caller additionally uses no_log because secrets enter via env.
        print(f"vaultwarden enrollment failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
