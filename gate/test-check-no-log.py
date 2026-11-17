#!/usr/bin/env python3
"""Run gate/check-no-log.py on scratch task files: a hidden assert, a hidden stat inside a
block and a hidden debug each fail; the same tasks unhidden, an assert whose `no_log` line
names its secret, a templated `no_log` and a hidden command pass."""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "gate" / "check-no-log.py"

FAILING = {
    "assert": """- name: Require a value
  ansible.builtin.assert:
    that: value | length > 0
    fail_msg: "{{ instance }} has no value."
  no_log: true
""",
    "block": """- name: Phase
  block:
    - name: Check the file
      ansible.builtin.stat:
        path: /etc/demo
      no_log: True
""",
    "debug": """- name: Show
  debug:
    msg: done
  no_log: yes
""",
}

PASSING = """- name: Require a value
  ansible.builtin.assert:
    that: value | length > 0
- name: Require each client
  ansible.builtin.assert:
    that: item.port > 0
  loop: "{{ clients }}"
  no_log: true  # loop items carry the client password
- name: Conditional
  ansible.builtin.assert:
    that: true
  no_log: "{{ drill_exec_secret | default(false) | bool }}"
- name: Call the API
  ansible.builtin.command:
    argv: [demo, "{{ token }}"]
  no_log: true
"""


def run(text: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tasks.yml"
        path.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, str(CHECK), str(path)],
                              capture_output=True, text=True, check=False)


def main() -> int:
    for label, text in FAILING.items():
        result = run(text)
        assert result.returncode == 1, (label, result.stdout, result.stderr)
        assert "hides its failure" in result.stderr, (label, result.stderr)
    result = run(PASSING)
    assert result.returncode == 0, result.stderr
    assert "1 hidden" in result.stdout and "0 without" in result.stdout, result.stdout
    result = subprocess.run([sys.executable, str(CHECK)], capture_output=True, text=True,
                            check=False, cwd=REPO)
    assert result.returncode == 0, result.stderr
    print("check-no-log: failing and passing fixtures behave; repository clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
