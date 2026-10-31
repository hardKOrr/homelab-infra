#!/usr/bin/env python3
"""Brief logs keep the findings and drop the rest, and a full log is unchanged.

LAB_LOG_BRIEF=1 makes the homelab callback print a TASK banner only for a task that reports
something; rundeck/log-brief.py applies the same cut to a followed Rundeck log.
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

repo = pathlib.Path(__file__).resolve().parent.parent
python = os.environ.get("GATE_PYTHON", sys.executable)
playbook_bin = os.environ.get("GATE_ANSIBLE_PLAYBOOK", str(pathlib.Path(python).parent / "ansible-playbook"))

PLAY = """\
- hosts: localhost
  gather_facts: false
  tasks:
    - name: Quiet task
      ansible.builtin.set_fact: {a: 1}
    - name: Reports a finding
      ansible.builtin.debug: {msg: finding-text}
    - name: Changes something
      ansible.builtin.command: "true"
    - name: Loop that changes
      ansible.builtin.command: "true"
      loop: [one]
    - name: Quiet again
      ansible.builtin.set_fact: {b: 2}
    - name: Fails loudly
      ansible.builtin.fail: {msg: boom-text}
"""


def run_playbook(brief):
    with tempfile.TemporaryDirectory() as tmp:
        book = pathlib.Path(tmp, "play.yml")
        book.write_text(PLAY)
        env = {
            **os.environ,
            "ANSIBLE_CONFIG": str(repo / "ansible" / "ansible.cfg"),
            "ANSIBLE_INVENTORY": str(repo / "gate" / "fixtures" / "localhost.ini"),
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_LOCALHOST_WARNING": "False",
        }
        env.pop("LAB_LOG_BRIEF", None)
        if brief:
            env["LAB_LOG_BRIEF"] = "1"
        out = subprocess.run([playbook_bin, "-i", "localhost,", "-c", "local", str(book)],
                             env=env, capture_output=True, text=True, cwd=repo / "ansible")
        assert out.returncode != 0, "the failing task must fail the run"
        return out.stdout


def banners(text):
    return [line.split("[", 1)[1].split("]", 1)[0] for line in text.splitlines() if line.startswith("TASK [")]


full = run_playbook(False)
assert banners(full) == ["Quiet task", "Reports a finding", "Changes something", "Loop that changes",
                         "Quiet again", "Fails loudly"], banners(full)

brief = run_playbook(True)
assert banners(brief) == ["Reports a finding", "Changes something", "Loop that changes", "Fails loudly"], banners(brief)
for needle in ("finding-text", "boom-text", "HOMELAB RESULT", "FAILED"):
    assert needle in brief, f"brief log lost {needle!r}:\n{brief}"
assert len(brief) < len(full)

spec = importlib.util.spec_from_file_location("log_brief", repo / "rundeck" / "log-brief.py")
log_brief = importlib.util.module_from_spec(spec)
spec.loader.exec_module(log_brief)

raw = [
    "\x1b[1;36m[lab-run]\x1b[0m revision abc\n",
    "/opt/x/connectionpool.py:1: InsecureRequestWarning: Unverified HTTPS request\n",
    "  warnings.warn(\n",
    "TASK [Quiet] ***\n",
    "\x1b[0;33mTASK [Changes] ***\x1b[0m\n",
    "\x1b[0;33mchanged: [localhost]\x1b[0m\n",
    "TASK [Fails] ***\n",
    "fatal: [localhost]: FAILED! => boom\n",
    "TASK [Trailing quiet] ***\n",
]
assert list(log_brief.brief(raw)) == [
    "[lab-run] revision abc",
    "TASK [Changes] ***",
    "changed: [localhost]",
    "TASK [Fails] ***",
    "fatal: [localhost]: FAILED! => boom",
], list(log_brief.brief(raw))

print("brief logs keep findings and failures, drop silent banners, and leave the full log whole")
