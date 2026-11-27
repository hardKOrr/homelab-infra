#!/usr/bin/env python3
"""Writers of config/.generated/facts.yml hold facts-lock.py across read, merge and write.

Two processes each add their own key to one temporary facts.yml, the way the registry
writers do (read, merge, write mode 0600), many times; every round ends with both keys.
Also checks every task that rewrites facts.yml takes the lock.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "ansible/scripts/facts-lock.py"
ROUNDS = 40

WRITER = """
import subprocess, sys, os, yaml
lock, path, key = sys.argv[1:4]
tok = subprocess.check_output([sys.executable, lock, "acquire", path], text=True).strip()
try:
    try:
        data = yaml.safe_load(open(path)) or {}
    except FileNotFoundError:
        data = {}
    import time; time.sleep(0.01)
    data = {**data, key: {"host": key}}
    with open(path, "w") as f:
        yaml.safe_dump(data, f)
    os.chmod(path, 0o600)
finally:
    subprocess.check_call([sys.executable, lock, "release", path, tok])
"""


def main() -> int:
    for name in ("ansible/tasks/bootstrap/write-generated-facts.yml",
                 "ansible/tasks/unwiring/registry.yml",
                 "ansible/tasks/kubernetes/remove-cluster.yml"):
        text = (ROOT / name).read_text()
        if "facts-lock.yml" not in text or "facts_lock_action: release" not in text:
            print(f"FAIL {name} does not take and release the facts lock")
            return 1
    with tempfile.TemporaryDirectory() as tmp:
        facts = os.path.join(tmp, "facts.yml")
        for n in range(ROUNDS):
            if os.path.exists(facts):
                os.unlink(facts)
            procs = [subprocess.Popen([sys.executable, "-c", WRITER, str(LOCK), facts, k])
                     for k in (f"a{n}", f"b{n}")]
            if any(p.wait() for p in procs):
                print("FAIL a writer exited non-zero")
                return 1
            got = set(yaml.safe_load(open(facts)))
            if got != {f"a{n}", f"b{n}"}:
                print(f"FAIL round {n}: facts.yml holds {sorted(got)}")
                return 1
            if os.path.exists(facts + ".lock"):
                print(f"FAIL round {n}: lock left behind")
                return 1
            if oct(os.stat(facts).st_mode & 0o777) != "0o600":
                print("FAIL mode is not 0600")
                return 1
    print(f"OK {ROUNDS} rounds kept both keys")
    return 0


sys.exit(main())
