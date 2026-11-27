#!/usr/bin/env python3
"""Writers of config/.generated/facts.yml hold facts-lock.py across read, merge and write.

Two processes each add their own key to one temporary facts.yml, the way the registry
writers do (read, merge, write mode 0600), many times; a killed holder or exited owner frees the lock; every round ends with both keys.
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


def recovery() -> bool:
    """A holder killed outright, or whose owner exits, frees the lock; a waiter blocks."""
    import signal
    import time

    with tempfile.TemporaryDirectory() as tmp:
        facts = os.path.join(tmp, "facts.yml")
        env = {**os.environ, "FACTS_LOCK_OWNER_PID": str(os.getpid())}
        run = lambda *a: subprocess.check_output([sys.executable, str(LOCK), *a], text=True, env=env).strip()
        first = int(run("acquire", facts))
        waiter = subprocess.Popen([sys.executable, str(LOCK), "acquire", facts],
                                  stdout=subprocess.PIPE, text=True, env=env)
        time.sleep(1)
        if waiter.poll() is not None:
            print("FAIL a second acquire did not wait for the held lock")
            return False
        os.kill(first, signal.SIGKILL)  # holder dies without releasing
        try:
            token = waiter.communicate(timeout=10)[0].strip()
        except subprocess.TimeoutExpired:
            print("FAIL waiter never got the lock after the holder died")
            waiter.kill()
            return False
        run("release", facts, token)
        # an owner that exits (killed run) frees the lock too
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        env["FACTS_LOCK_OWNER_PID"] = str(owner.pid)
        run("acquire", facts)
        owner.kill()
        owner.wait()
        env["FACTS_LOCK_OWNER_PID"] = str(os.getpid())
        try:
            subprocess.check_output([sys.executable, str(LOCK), "acquire", facts],
                                    text=True, env=env, timeout=10)
        except subprocess.TimeoutExpired:
            print("FAIL lock stayed held after its owner exited")
            return False
    return True


def main() -> int:
    for name in ("ansible/tasks/bootstrap/write-generated-facts.yml",
                 "ansible/tasks/unwiring/registry.yml",
                 "ansible/tasks/kubernetes/remove-cluster.yml"):
        text = (ROOT / name).read_text()
        if "facts-lock.yml" not in text or "facts_lock_action: release" not in text:
            print(f"FAIL {name} does not take and release the facts lock")
            return 1
    boot = (ROOT / "rundeck/bootstrap-rundeck.sh").read_text()
    if boot.index('flock 9') > boot.index('"$VENV_DIR/bin/python3" - "$FACTS"'):
        print("FAIL bootstrap-rundeck.sh writes the runner key before taking the facts lock")
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
            if oct(os.stat(facts).st_mode & 0o777) != "0o600":
                print("FAIL mode is not 0600")
                return 1
    if not recovery():
        return 1
    print(f"OK {ROUNDS} rounds kept both keys")
    return 0


sys.exit(main())
