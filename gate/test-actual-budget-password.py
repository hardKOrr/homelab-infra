#!/usr/bin/env python3
"""Exercise the native terminal prompt contract and its failure boundary."""

import importlib.util
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "actual_password", repo / "ansible/files/actual-budget-reset-password.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

# Match Actual's raw-terminal contract: password bytes and carriage return must be
# separate events, and the second prompt only begins after the first is complete.
fixture = '''import os,sys,tty
tty.setraw(sys.stdin.fileno())
answers=[]
for prompt in ["Enter a password, then press enter: ",
               "Enter the password again, then press enter: "]:
    os.write(1,prompt.encode())
    answer=os.read(0,4096)
    os.write(1,b"*")
    assert os.read(0,4096)==b"\\r", "return must arrive separately"
    answers.append(answer)
assert answers[0]==answers[1] and answers[0]==sys.argv[1].encode()
os.write(1,b"Password changed!\\n")
'''


def main():
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "native-prompt.py"
        script.write_text(fixture)
        helper.reset_password([sys.executable, str(script), "test password $quoted"],
                              "test password $quoted", timeout=5)
        for command in ([sys.executable, "-c", "raise SystemExit(1)"],
                        [sys.executable, "-c", "print('unexpected prompt')"],
                        [sys.executable, "-c", "import time; time.sleep(30)"]):
            try:
                helper.reset_password(command, "private-test-input", timeout=0.5)
            except RuntimeError as error:
                assert "private-test-input" not in str(error)
            else:
                raise AssertionError("failed or incomplete reset accepted")
    print("Actual Budget: terminal password exchange and failures passed")


if __name__ == "__main__":
    main()
