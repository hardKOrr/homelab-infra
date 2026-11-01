#!/usr/bin/env python3
"""Run gate/check-structure.py on a scratch repository: a clean tree passes; a file
growing past its ceiling, a new over-budget file, an exception that meets its rule, a
directory nobody maps, an unreachable README, a restated passage, a literal app-name
comparison in a playbook and a missing per-app path each fail."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
PASSAGE = "every word of this sentence is the same in both documents so it counts once more"

BASE = {
    "AGENTS.md": "# Agents\n\n[ansible](ansible/README.md) [gate](gate/README.md) "
                 "[examples](config.example/README.md) [readme](README.md)\n",
    "README.md": "`catalog/`\n",
    "gate/README.md": "# Gate\n",
    "catalog/applications.yml": "applications:\n  demo: {}\n  other: {}\n",
    "ansible/README.md": "# Ansible\n\n`roles/`, `vars/app-defaults/`, `playbooks/apps/`.\n",
    "ansible/playbooks/apps/demo.yml": "- hosts: localhost\n",
    "ansible/playbooks/apps/other.yml": "- hosts: localhost\n",
    "ansible/playbooks/apps/remove.yml": "- hosts: localhost\n" + "# filler\n" * 340,
    "ansible/vars/app-defaults/demo.yml": "app: {}\n",
    "ansible/vars/app-defaults/other.yml": "app: {}\n",
    "ansible/roles/demo/tasks/main.yml": "- ansible.builtin.meta: noop\n",
    "ansible/roles/other/tasks/main.yml": "- ansible.builtin.meta: noop\n",
    "config.example/README.md": "# Examples\n\n`apps/`\n",
    "config.example/apps/demo.example.yml": "app: {}\n",
    "config.example/apps/other.example.yml": "app: {}\n",
}
EXCEPTIONS = {"size": {"ansible/playbooks/apps/remove.yml": 341}}


def run(files: dict[str, str], exceptions: dict) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for path, content in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(content)
        shutil.copy(REPO / "gate/check-structure.py", root / "gate/check-structure.py")
        (root / "gate/structure.yml").write_text(yaml.safe_dump(exceptions))
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        return subprocess.run([sys.executable, "gate/check-structure.py"], cwd=root,
                              capture_output=True, text=True)


def expect(name: str, files: dict, exceptions: dict, ok: bool, needle: str = "") -> None:
    result = run(files, exceptions)
    passed = result.returncode == 0
    if passed != ok or needle not in result.stdout:
        print(f"FAIL {name}: exit {result.returncode}, wanted {'0' if ok else '1'} "
              f"with {needle!r}\n{result.stdout}{result.stderr}")
        sys.exit(1)
    print(f"ok   {name}")


expect("clean tree", BASE, EXCEPTIONS, True)
expect("ceiling exceeded", {**BASE, "ansible/playbooks/apps/remove.yml": BASE[
    "ansible/playbooks/apps/remove.yml"] + "# more\n"}, EXCEPTIONS, False, "grew to 342")
expect("new over-budget file", {**BASE, "ansible/roles/demo/tasks/big.yml": "- x\n" * 301},
       EXCEPTIONS, False, "big.yml is 301")
expect("exception now met", {**BASE, "ansible/playbooks/apps/remove.yml": "- hosts: all\n"},
       EXCEPTIONS, False, "now meets the rule")
expect("unmapped directory", {**BASE, "ansible/tasks/thing/a.yml": "- x\n"}, EXCEPTIONS,
       False, "readme: ansible/tasks")
expect("mapped by the nearest README",
       {**BASE, "ansible/tasks/thing/a.yml": "- x\n",
        "ansible/README.md": BASE["ansible/README.md"] + "`tasks/thing/`\n"}, EXCEPTIONS, True)
expect("unreachable README", {**BASE, "guide/README.md": "# Docs\n",
                              "README.md": "`catalog/` `guide/`\n"}, EXCEPTIONS, False, "reach: guide/README.md")
expect("restated passage", {**BASE, "gate/README.md": f"# Gate\n{PASSAGE}\n",
                            "ansible/README.md": BASE["ansible/README.md"] + PASSAGE},
       EXCEPTIONS, False, "shared: ansible/README.md gate/README.md")
expect("app literal in a shared playbook",
       {**BASE, "ansible/playbooks/apps/remove.yml": BASE["ansible/playbooks/apps/remove.yml"]
        + "      when: (remove_app | default(instance)) == 'demo'\n"},
       {"size": {"ansible/playbooks/apps/remove.yml": 342}}, False,
       "literal: ansible/playbooks/apps/remove.yml")
expect("provider comparison is not an app literal",
       {**BASE, "ansible/playbooks/apps/demo.yml": "- when: dns.provider == 'other'\n"},
       EXCEPTIONS, True)
expect("missing per-app path", {k: v for k, v in BASE.items() if "roles/other" not in k},
       EXCEPTIONS, False, "has no ansible/roles/other")
expect("role alias", {k: v for k, v in BASE.items() if "roles/other" not in k},
       {**EXCEPTIONS, "role_aliases": {"other": "demo"}}, True)
