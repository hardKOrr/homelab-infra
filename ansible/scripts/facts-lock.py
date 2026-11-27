#!/usr/bin/env python3
"""Hold one lock across a read, merge and write of config/.generated/facts.yml.

    facts-lock.py acquire <facts.yml>   waits, then prints a token
    facts-lock.py release <facts.yml> <token>

The lock is the file `<facts.yml>.lock`, created with O_EXCL, so two live runs that
overlap take turns instead of one overwriting the other's key. A lock whose owner never
released it (a killed run) is broken once it is older than STALE seconds. Release removes
the lock only when the token still matches, so a run never frees a lock it lost.
"""

import os
import secrets
import sys
import time

STALE = 300
WAIT = 600


def acquire(path: str) -> str:
    lock = path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock)), mode=0o750, exist_ok=True)
    token = secrets.token_hex(8)
    deadline = time.monotonic() + WAIT
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if time.time() - os.stat(lock).st_mtime > STALE:
                    os.unlink(lock)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                sys.exit(f"timed out waiting for {lock}")
            time.sleep(0.05)
            continue
        with os.fdopen(fd, "w") as handle:
            handle.write(token)
        return token


def release(path: str, token: str) -> None:
    lock = path + ".lock"
    try:
        with open(lock) as handle:
            if handle.read() != token:
                return
        os.unlink(lock)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "acquire":
        print(acquire(sys.argv[2]))
    elif len(sys.argv) == 4 and sys.argv[1] == "release":
        release(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
