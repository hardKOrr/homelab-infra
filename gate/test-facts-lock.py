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
import subprocess, sys, os, yaml, time
lock, path, key = sys.argv[1:4]
tok = subprocess.check_output([sys.executable, lock, "acquire", path], text=True).strip()
try:
    try:
        data = yaml.safe_load(open(path)) or {}
    except FileNotFoundError:
        data = {}
    time.sleep(0.01)
    data = {**data, key: {"host": key}}
    subprocess.run([sys.executable, lock, "commit", path, tok], input=yaml.safe_dump(data),
                   text=True, check=True, stdout=subprocess.DEVNULL)
finally:
    subprocess.check_call([sys.executable, lock, "release", path, tok])
"""


def token_probe(token: str) -> bytes:
    """A valid request whose client disconnects before the reply: the holder's send fails."""
    return token.partition(".")[2].encode() + b" 1"


def alive_pid(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def recovery() -> bool:
    """A holder killed outright, or whose owner exits, frees the lock; a waiter blocks."""
    import signal
    import time

    with tempfile.TemporaryDirectory() as tmp:
        facts = os.path.join(tmp, "facts.yml")
        env = {**os.environ, "FACTS_LOCK_OWNER_PID": str(os.getpid())}
        run = lambda *a: subprocess.check_output([sys.executable, str(LOCK), *a], text=True, env=env).strip()
        first_token = run("acquire", facts)
        first = int(first_token.split(".")[0])
        waiter = subprocess.Popen([sys.executable, str(LOCK), "acquire", facts],
                                  stdout=subprocess.PIPE, text=True, env=env)
        time.sleep(1)
        if waiter.poll() is not None:
            print("FAIL a second acquire did not wait for the held lock")
            return False
        import socket
        forged = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        forged.connect(b"\0facts-lock-" + str(first).encode())
        forged.sendall(b"not-the-secret 1\n")
        if not forged.makefile("rb").readline().startswith(b"error"):
            print("FAIL the holder accepted a request without the transaction secret")
            return False
        forged.close()
        # clients that disconnect without reading, or send garbage, must not stop the holder
        for payload in (b"x" * 5000 + b"\n", b"\xff\xfe\n", b"", token_probe(first_token) + b"\n"):
            rude = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            rude.connect(b"\0facts-lock-" + str(first).encode())
            rude.sendall(payload)
            rude.close()
        time.sleep(0.5)
        if not alive_pid(first):
            print("FAIL a disconnecting client stopped the lock holder")
            return False
        ok = subprocess.run([sys.executable, str(LOCK), "commit", facts, first_token],
                            input="first: 1\n", text=True, env=env, capture_output=True)
        if ok.returncode or yaml.safe_load(open(facts)) != {"first": 1}:
            print("FAIL the original holder could not commit after clients disconnected")
            return False
        os.unlink(facts)
        # another process squatting the holder's socket name must not be trusted
        fake_id = 4000000
        squat = subprocess.Popen([sys.executable, "-c", (
            "import socket,sys;s=socket.socket(socket.AF_UNIX);"
            f"s.bind(b'\\0facts-lock-{fake_id}');s.listen(1);print('up',flush=True);"
            "c,_=s.accept();c.recv(100);c.sendall(b'unchanged\\n')")],
            stdout=subprocess.PIPE, text=True)
        squat.stdout.readline()
        fake = subprocess.run([sys.executable, str(LOCK), "commit", os.path.join(tmp, "squat.yml"),
                               f"{fake_id}.secret"], input="x: 1\n", text=True, env=env, capture_output=True)
        squat.kill()
        if fake.returncode == 0 or "not served by lock holder" not in fake.stderr:
            print("FAIL commit trusted a reply from a process that is not the lock holder")
            return False
        stale_view = "stale: snapshot\n"
        os.kill(first, signal.SIGKILL)  # holder dies without releasing
        try:
            token = waiter.communicate(timeout=10)[0].strip()
        except subprocess.TimeoutExpired:
            print("FAIL waiter never got the lock after the holder died")
            waiter.kill()
            return False
        # the first transaction lost its lock: its commit must not reach facts.yml
        stale = subprocess.run([sys.executable, str(LOCK), "commit", facts, first_token],
                               input=stale_view, text=True, env=env, capture_output=True)
        if stale.returncode == 0 or os.path.exists(facts):
            print("FAIL a transaction that lost its lock was able to write facts.yml")
            return False
        if [f for f in os.listdir(tmp) if f.endswith(".new")]:
            print("FAIL a refused commit left a staged file")
            return False
        # a stale token (right pid, wrong secret: a reused pid) neither commits nor releases
        stale_tok = token.split(".")[0] + ".deadbeef"
        bad = subprocess.run([sys.executable, str(LOCK), "commit", facts, stale_tok],
                             input="stale: 1\n", text=True, env=env, capture_output=True)
        run("release", facts, stale_tok)
        if bad.returncode == 0 or not alive_pid(int(token.split(".")[0])):
            print("FAIL a stale token committed or released the current holder")
            return False
        live = subprocess.run([sys.executable, str(LOCK), "commit", facts, token],
                              input="live: 1\n", text=True, env=env, capture_output=True)
        if live.returncode or live.stdout.strip() != "changed" or oct(os.stat(facts).st_mode & 0o777) != "0o600":
            print("FAIL the live holder's commit did not publish 0600 content")
            return False
        again = subprocess.run([sys.executable, str(LOCK), "commit", facts, token],
                               input="live: 1\n", text=True, env=env, capture_output=True)
        if again.stdout.strip() != "unchanged":
            print("FAIL an identical commit did not report unchanged")
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
        if "facts-lock.yml" not in text or "facts_lock_action: release" not in text or "facts_lock_action: commit" not in text or "ansible.builtin.copy" in text:
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
