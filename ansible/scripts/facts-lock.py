#!/usr/bin/env python3
"""Hold one lock across a read, merge and write of config/.generated/facts.yml.

    facts-lock.py acquire <facts.yml>   waits for the lock, prints a token
    facts-lock.py commit  <facts.yml> <token>   new content on stdin; prints changed|unchanged
    facts-lock.py release <facts.yml> <token>

The lock is an OS flock on the persistent file `<facts.yml>.lock`. Ansible runs each task
in its own process, so `acquire` starts a small detached holder process that owns the
flock for the whole transaction and `release` stops it. The kernel drops the flock when the
holder exits, however it exits, and the holder exits when the owning ansible-playbook run
is gone. The lock is therefore never broken by a timeout: a slow writer keeps it, and a
dead one cannot strand it. The token is the holder's pid.

The holder, not the writer, publishes the file: `commit` stages the content beside
facts.yml and asks the holder to rename it into place. A transaction whose holder has died
(and whose lock another writer may now hold) cannot reach the holder, so its stale snapshot
is never written and the task fails.
"""

import fcntl
import os
import signal
import socket
import subprocess
import sys
import time

WAIT = 900  # seconds `acquire` waits for another writer


def owner_pid() -> int:
    """The topmost ansible-playbook ancestor, else the caller's parent."""
    if os.environ.get("FACTS_LOCK_OWNER_PID"):
        return int(os.environ["FACTS_LOCK_OWNER_PID"])
    pid, found = os.getppid(), os.getppid()
    while pid > 1:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                cmd = handle.read().replace(b"\0", b" ")
            with open(f"/proc/{pid}/stat") as handle:
                ppid = int(handle.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
        if b"ansible-playbook" in cmd:
            found = pid
        pid = ppid
    return found


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def hold(path: str, owner: int) -> None:
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if not alive(owner):
                os._exit(0)
            time.sleep(0.02)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_name(os.getpid()))
    srv.listen(4)
    srv.settimeout(0.2)
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    sys.stdout.close()
    while alive(owner):
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        with conn:
            conn.settimeout(10)
            staged = conn.makefile("rb").readline().decode().rstrip("\n")
            conn.sendall(publish(path, staged).encode())
    os._exit(0)


def sock_name(pid: int) -> bytes:
    return b"\0facts-lock-" + str(pid).encode()


def publish(path: str, staged: str) -> str:
    """Rename the staged file over facts.yml unless it already has that content."""
    if os.path.dirname(os.path.abspath(staged)) != os.path.dirname(os.path.abspath(path)):
        return "error: staged file is not beside facts.yml\n"
    try:
        with open(staged, "rb") as new:
            data = new.read()
        try:
            with open(path, "rb") as old:
                same = old.read() == data and (os.stat(path).st_mode & 0o777) == 0o600
        except FileNotFoundError:
            same = False
        if same:
            os.unlink(staged)
            return "unchanged\n"
        os.chmod(staged, 0o600)
        os.replace(staged, path)
        return "changed\n"
    except OSError as err:
        return f"error: {err}\n"


def commit(path: str, token: str) -> str:
    if not token.isdigit():
        sys.exit("no facts lock is held by this transaction")
    staged = f"{path}.{token}.{os.getpid()}.new"
    fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(sys.stdin.buffer.read())
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(30)
        client.connect(sock_name(int(token)))
        client.sendall(staged.encode() + b"\n")
        reply = client.makefile("rb").readline().decode().strip()
    except OSError as err:
        reply = f"error: the lock holder is gone ({err})"
    if reply not in ("changed", "unchanged"):
        if os.path.exists(staged):
            os.unlink(staged)
        sys.exit(f"facts.yml not written: {reply}")
    return reply


def acquire(path: str) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o750, exist_ok=True)
    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "hold", path, str(owner_pid())],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True)
    deadline = time.monotonic() + WAIT
    fd = child.stdout.fileno()
    os.set_blocking(fd, False)
    line = b""
    while not line.endswith(b"\n"):
        try:
            chunk = os.read(fd, 64)
        except BlockingIOError:
            chunk = None
        if chunk:
            line += chunk
        elif chunk == b"" or child.poll() is not None:
            sys.exit(f"lock holder for {path}.lock exited before it held the lock")
        elif time.monotonic() > deadline:
            child.kill()
            sys.exit(f"timed out waiting for {path}.lock")
        else:
            time.sleep(0.02)
    return child.pid


def release(path: str, token: str) -> None:
    if not token.isdigit():
        return
    pid = int(token)
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            cmd = handle.read().split(b"\0")
    except OSError:
        return
    if b"hold" not in cmd or path.encode() not in cmd:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(500):
        if not alive(pid):
            return
        time.sleep(0.02)
    sys.exit(f"lock holder {pid} did not stop")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "acquire":
        print(acquire(sys.argv[2]))
    elif len(sys.argv) == 4 and sys.argv[1] == "commit":
        print(commit(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) == 4 and sys.argv[1] == "release":
        release(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "hold":
        hold(sys.argv[2], int(sys.argv[3]))
    else:
        sys.exit(__doc__)
