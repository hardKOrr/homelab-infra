#!/usr/bin/env python3
"""Drive Actual's native, terminal-only password utility without logging secrets."""

import errno
import os
import pty
import select
import subprocess
import sys
import termios
import time


def reset_password(command, password, timeout=120):
    master, slave = pty.openpty()
    settings = termios.tcgetattr(slave)
    settings[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, settings)
    process = None
    output = b""
    pending = b""
    prompts = [b"Enter a password, then press enter: ",
               b"Enter the password again, then press enter: "]
    prompt_index = 0
    waiting_for_mask = False
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        slave = None
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], min(0.2, max(0, deadline - time.monotonic())))
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    break
                output += chunk
                pending += chunk
                if waiting_for_mask and b"*" in pending:
                    # Actual examines the first byte of each input event. The return
                    # must arrive separately from the password, after it was consumed.
                    os.write(master, b"\r")
                    pending = b""
                    waiting_for_mask = False
                    prompt_index += 1
                elif prompt_index < len(prompts) and prompts[prompt_index] in pending:
                    os.write(master, password.encode("utf-8"))
                    pending = b""
                    waiting_for_mask = True
            if process.poll() is not None and not ready:
                break
        remaining = max(0.01, deadline - time.monotonic())
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise RuntimeError("password utility timed out") from None
        if code != 0 or prompt_index != 2 or not any(
            message in output for message in [b"Password set!", b"Password changed!"]
        ):
            raise RuntimeError("password utility did not confirm success")
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        os.close(master)
        if slave is not None:
            os.close(slave)


def main():
    if len(sys.argv) != 3:
        print("usage: actual-budget-reset-password.py <project-directory> <service>", file=sys.stderr)
        return 1
    password = sys.stdin.readline().rstrip("\r\n")
    if not password or any(character in password for character in "\r\n\x00"):
        print("Actual Budget password input is invalid", file=sys.stderr)
        return 1
    command = ["docker", "compose", "--project-directory", sys.argv[1], "exec",
               sys.argv[2], "node", "/app/src/scripts/reset-password.js"]
    try:
        reset_password(command, password)
    except (OSError, RuntimeError):
        # Never relay utility output: it could contain the input or application state.
        print("Actual Budget native password initialization failed", file=sys.stderr)
        return 1
    print("Actual Budget native password initialized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
