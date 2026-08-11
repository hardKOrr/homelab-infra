#!/usr/bin/env python3
"""Render a qBittorrent WebUI password into the value its config file stores.

Reads the plaintext password on STDIN and writes qBittorrent's stored form to
STDOUT:

    @ByteArray(<base64 salt>:<base64 key>)

which is what `WebUI\\Password_PBKDF2` holds in qBittorrent.conf. The parameters
are qBittorrent's own (base/utils/password.cpp): PBKDF2-HMAC-SHA512, 100000
iterations, a 16-byte salt and a 64-byte derived key.

STDIN, not argv and not the environment: this is the credential every *arr in the
lab authenticates to the download client with, and both of the other channels are
readable from outside the process.

THE SALT IS DERIVED FROM THE PASSWORD, not random. qBittorrent accepts any salt —
it reads the one stored alongside the key — so randomness buys nothing here and
costs idempotence: a fresh salt every run means a different stored value every
run, which reads as drift and would restart the container on every deploy. The
role compares the rendered value with the one on disk to decide whether anything
changed, and that comparison only means something if the rendering is stable.
"""

from __future__ import annotations

import base64
import hashlib
import sys

ITERATIONS = 100_000
SALT_BYTES = 16
KEY_BYTES = 64


def render(password: str) -> str:
    salt = hashlib.sha256(password.encode("utf-8")).digest()[:SALT_BYTES]
    key = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, ITERATIONS, KEY_BYTES)
    return "@ByteArray({}:{})".format(
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(key).decode("ascii"),
    )


def main() -> int:
    password = sys.stdin.read()
    # Only a trailing newline is stripped. A password may legitimately end in a
    # space, and .strip() would silently hash something the operator never set.
    password = password[:-1] if password.endswith("\n") else password
    if not password:
        print("no password on stdin", file=sys.stderr)
        return 2
    sys.stdout.write(render(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
