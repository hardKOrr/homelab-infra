#!/usr/bin/env python3
"""Remove App reaches per-app behavior only through role hooks: remove.yml runs after the
Compose project stops and before the shared PBS client settings and project directory
go; remove-devices.yml runs after the data paths and before the shared device detach.
Each hook's tasks run for its own app only, and a renamed instance (app != instance)
resolves to the same plan as the app's own name."""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remove_plan import plan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APPS = list(yaml.safe_load((ROOT / "catalog/applications.yml").read_text())["applications"])


def index(steps, name):
    found = [i for i, step in enumerate(steps) if step["name"] == name]
    assert len(found) == 1, f"{name!r} appears {len(found)} times"
    return found[0]


def from_hook(steps, app, file):
    """Indexes of the steps that came through roles/<app>/tasks/<file>."""
    hook = f"ansible/roles/{app}/tasks/{file}"
    return [i for i, step in enumerate(steps) if step["via"][:1] == [hook]]


plans = {app: plan(ROOT, app) for app in APPS}

for app in sorted({p.parent.parent.name for p in (ROOT / "ansible/roles").glob("*/tasks/remove*.yml")}):
    assert app in APPS, f"roles/{app} has a removal hook but is not a catalog app"
    steps = plans[app]
    stop = index(steps, "Stop and remove the containers")
    pbs = index(steps, "Remove the instance's PBS client settings")
    data = index(steps, "Delete bind-mounted data and config paths")
    shared = index(steps, "Detach the app's project-owned shared device")
    for file, (low, high) in (("remove.yml", (stop, pbs)), ("remove-devices.yml", (data, shared))):
        path = ROOT / f"ansible/roles/{app}/tasks/{file}"
        if not path.is_file():
            continue
        found = from_hook(steps, app, file)
        assert found, f"{app} {file}: no step runs from the hook"
        for where in found:
            assert low < where < high, f"{app} {file}: step {where} is not in ({low}, {high})"
        for other in APPS:
            if other != app:
                assert not from_hook(plans[other], app, file), f"{app} {file} also runs for {other}"
    print(f"ok   {app}: hooks run in place and only for {app}")

for app in APPS:
    assert plan(ROOT, app, f"{app}-renamed") == plans[app], f"{app}: a renamed instance differs"
print(f"ok   {len(APPS)} apps: a renamed instance resolves to the app's own plan")

playbook = (ROOT / "ansible/playbooks/apps/remove.yml").read_text()
assert "remove_app" not in playbook, "remove_app is never set; use _rm_app_name"
print("ok   remove.yml resolves the app only through _rm_app_name")
