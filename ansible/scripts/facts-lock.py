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
dead one cannot strand it. The token is `<holder pid>.<secret>`; the secret is generated per
transaction and handed to the holder on its stdin, never on a command line.

The holder, not the writer, publishes the file: `commit` stages the content beside
facts.yml and asks the holder to rename it into place. A transaction whose holder has died
(and whose lock another writer may now hold) cannot reach the holder, so its stale snapshot
is never written and the task fails.
"""

import fcntl
import os
import hmac
import secrets
import signal
import socket
import stat
import struct
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
    secret = sys.stdin.readline().strip()
    if not secret:
        os._exit(1)
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
            reply = handle(conn, path, secret)
            conn.sendall(reply.encode())
        if reply == "released\n":
            os._exit(0)
    os._exit(0)


def sock_name(pid: int) -> bytes:
    return b"\0facts-lock-" + str(pid).encode()


def handle(conn: socket.socket, path: str, secret: str) -> str:
    """Serve one request: `<secret> <n>`, from a peer running as this user."""
    cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    if struct.unpack("3i", cred)[1] != os.getuid():
        return "error: peer is not this user\n"
    parts = conn.makefile("rb").readline().decode().split()
    if len(parts) != 2 or not hmac.compare_digest(parts[0], secret):
        return "error: not this transaction\n"
    if parts[1] == "release":
        return "released\n"
    if not parts[1].isdigit():
        return "error: bad request\n"
    # The only file the holder will publish is the one this transaction's commit stages.
    staged = f"{path}.{os.getpid()}.{parts[1]}.new"
    return publish(path, staged)


def publish(path: str, staged: str) -> str:
    """Rename the staged file over facts.yml unless it already has that content."""
    try:
        st = os.lstat(staged)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return "error: staged file is not a regular file of this user\n"
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
    pid, _, secret = token.partition(".")
    if not pid.isdigit() or not secret:
        sys.exit("no facts lock is held by this transaction")
    staged = f"{path}.{pid}.{os.getpid()}.new"
    fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(sys.stdin.buffer.read())
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(30)
        client.connect(sock_name(int(pid)))
        client.sendall(f"{secret} {os.getpid()}\n".encode())
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
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True)
    secret = secrets.token_hex(16)
    child.stdin.write(secret.encode() + b"\n")
    child.stdin.close()
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
    return f"{child.pid}.{secret}"


def release(path: str, token: str) -> None:
    """Ask the holder to stop. A token from another acquisition, even one whose pid the
    kernel reused, fails the secret check and releases nothing."""
    pid, _, secret = token.partition(".")
    if not pid.isdigit() or not secret:
        return
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(10)
        client.connect(sock_name(int(pid)))
        client.sendall(f"{secret} release\n".encode())
        reply = client.makefile("rb").readline().decode().strip()
    except OSError:
        return  # the holder is already gone, so the lock is already free
    if reply != "released":
        return
    for _ in range(500):
        if not alive(int(pid)):
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
