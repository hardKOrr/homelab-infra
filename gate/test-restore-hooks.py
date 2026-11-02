#!/usr/bin/env python3
"""Restore App reaches per-app behavior only through role hooks, each at its place:
restore-inputs.yml after the source n8n key and before the storage identity checks;
restore-provisioned.yml after a new target's stack is reapplied and before the target is
resolved; restore-config.yml after the target is resolved and before the restore runs.
Each hook runs only for its own role's apps, and only a role named after its app has a
hook that runs before the recovery capability names the role. The shared
tasks/recovery/rebase-target-path.yml rebases a path in a real play."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remove_plan import RESTORE, plan_all  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APPS = list(yaml.safe_load((ROOT / "catalog/applications.yml").read_text())["applications"])
ALIASES = yaml.safe_load((ROOT / "gate/structure.yml").read_text())["role_aliases"]
ANSIBLE_PLAYBOOK = os.environ.get("GATE_ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")


def role(app):
    return ALIASES.get(app) or app


WINDOWS = {
    "restore-inputs.yml": ("Resolve source n8n recovery key without exposing it in evidence",
                           "Resolve native guest storage identity scopes"),
    "restore-provisioned.yml": ("Reapply the effective stack to the provisioned restore target",
                                "Resolve the Docker or native-LXC target"),
    "restore-config.yml": ("Resolve the newly provisioned Docker or native-LXC target",
                           "Restore the application"),
}
EARLY = ("restore-inputs.yml", "restore-provisioned.yml")  # run before the capability resolves


def index(steps, name):
    found = [i for i, step in enumerate(steps) if step.get("name") == name]
    assert len(found) == 1, f"{name!r} appears {len(found)} times"
    return found[0]


def from_hook(steps, owner, file):
    hook = f"ansible/roles/{owner}/tasks/{file}"
    return [i for i, step in enumerate(steps) if step.get("via", [])[:1] == [hook]]


plans = {app: plan_all(ROOT, app, None, playbook=RESTORE, role=role(app)) for app in APPS}

for file in WINDOWS:
    owners = sorted(p.parent.parent.name for p in (ROOT / "ansible/roles").glob(f"*/tasks/{file}"))
    for owner in owners:
        apps = [app for app in APPS if role(app) == owner]
        assert apps, f"roles/{owner}/tasks/{file} belongs to no catalog app"
        if file in EARLY:
            assert apps == [owner], f"{file} in roles/{owner} would not run for {apps}"
        for app in apps:
            steps = plans[app]
            low, high = (index(steps, name) for name in WINDOWS[file])
            found = from_hook(steps, owner, file)
            assert found, f"{app}: roles/{owner}/tasks/{file} runs no step"
            assert all(low < where < high for where in found), f"{app} {file} runs out of place"
        for other in APPS:
            if role(other) != owner:
                assert not from_hook(plans[other], owner, file), f"{owner} {file} runs for {other}"
    print(f"ok   {file}: runs in place for {', '.join(owners)} only")


def rebase(app, **inputs):
    """Run tasks/recovery/rebase-target-path.yml in a real play; return restore_app_config.app.

    `app` maps keys to paths; a path holding a literal {{ instance }} is marked !unsafe, as a
    value read from a file may hold one."""
    paths = "".join(f"\n          {key}: !unsafe '{path}'" for key, path in app.items())
    with tempfile.TemporaryDirectory(prefix="homelab-rebase.") as directory:
        playbook = Path(directory) / "rebase.yml"
        playbook.write_text(f"""
- hosts: localhost
  gather_facts: false
  vars:
    instance: src
    _ra_source_instance: src
    _ra_target: dst
    restore_target_config:
        routing: {{proxy: none}}
        app:{paths}
  tasks:
    - name: Rebase
      ansible.builtin.include_tasks: {ROOT / "ansible/tasks/recovery/rebase-target-path.yml"}
      vars: {json.dumps(inputs)}
    - name: Record the result
      ansible.builtin.copy:
        content: "{{{{ restore_app_config | to_json }}}}"
        dest: {Path(directory) / "rebased.json"}
        mode: "0600"
""")
        result = subprocess.run(
            [ANSIBLE_PLAYBOOK, "-i", "localhost,", "-c", "local", str(playbook)],
            text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        rebased = json.loads((Path(directory) / "rebased.json").read_text())
        assert rebased["routing"] == {"proxy": "none"}, rebased
        return rebased["app"]


app = {"data_path": "/opt/src/data/{{ instance }}", "config_path": "/opt/src/config",
       "filestore_path": "/opt/odoo/src/{{ instance }}"}
plain = rebase(app, restore_rebase_key="config_path")
assert plain == {**app, "config_path": "/opt/dst/config"}, plain
kept = rebase(app, restore_rebase_key="data_path")
assert kept["data_path"] == "/opt/dst/data/{{ instance }}", kept
both = rebase(app, restore_rebase_key="data_path", restore_rebase_instance=True)
assert both["data_path"] == "/opt/dst/data/dst", both
odoo = rebase(app, restore_rebase_key="filestore_path", restore_rebase_prefix="/opt/odoo/",
              restore_rebase_instance=True)
assert odoo["filestore_path"] == "/opt/odoo/dst/dst", odoo
print("ok   rebase-target-path.yml rebases one key, its prefix and, when asked, {{ instance }}")
