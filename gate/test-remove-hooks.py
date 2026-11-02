#!/usr/bin/env python3
"""Remove App reaches per-app behavior only through role hooks, each at its place:
remove-inputs.yml before the named-database decision and any unwiring; remove-unwire.yml
after the provider unwiring and before the registry prune; remove-units.yml before the
native service stops; remove-native-data.yml after a native stop and before its data
paths; remove.yml after the Compose project stops and before the shared PBS client
settings and project directory go; remove-devices.yml after the data paths and before the
shared device detach. Each hook's tasks run for its own app only, a hook that only declares
`_rm_database_backend` makes Remove forget the backend's registry entry, and a renamed
instance (app != instance) resolves to the same plan as the app's own name."""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remove_plan import FLAGS, plan_all  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APPS = list(yaml.safe_load((ROOT / "catalog/applications.yml").read_text())["applications"])


def index(steps, name):
    found = [i for i, step in enumerate(steps) if step.get("name") == name]
    assert len(found) == 1, f"{name!r} appears {len(found)} times"
    return found[0]


def from_hook(steps, app, file):
    """Indexes of the steps that came through roles/<app>/tasks/<file>."""
    hook = f"ansible/roles/{app}/tasks/{file}"
    return [i for i, step in enumerate(steps) if step.get("via", [])[:1] == [hook]]


plans = {app: plan_all(ROOT, app) for app in APPS}

# Each hook's steps lie strictly between these two steps of the resolved plan.
WINDOWS = {
    "remove-inputs.yml": ("Apply the effective stack for removal",
                          "Decide whether Remove drops a named database"),
    "remove-unwire.yml": ("Unwire firewall", "Unregister from the service registry"),
    "remove-units.yml": ("Remove App | Stop", "Stop and disable every service unit"),
    "remove-native-data.yml": ("Report an already-removed service", "Delete the data and cache paths"),
    "remove.yml": ("Stop and remove the containers", "Remove the instance's PBS client settings"),
    "remove-devices.yml": ("Delete bind-mounted data and config paths",
                           "Detach the app's project-owned shared device"),
}


def position(steps, name):
    if name.startswith("Remove App | "):  # a play boundary
        found = [i for i, step in enumerate(steps) if str(step.get("play", "")).startswith(name)]
        assert len(found) == 1, f"{name!r} appears {len(found)} times"
        return found[0]
    return index(steps, name)


hooks = {p.name for p in (ROOT / "ansible/roles").glob("*/tasks/remove*.yml")} - {"remove-media.yml"}
assert hooks <= set(WINDOWS), f"unplaced removal hooks: {sorted(hooks - set(WINDOWS))}"

for app in sorted({p.parent.parent.name for p in (ROOT / "ansible/roles").glob("*/tasks/remove*.yml")}):
    assert app in APPS, f"roles/{app} has a removal hook but is not a catalog app"
    steps = plans[app]
    for file, (after, before) in WINDOWS.items():
        low, high = position(steps, after), position(steps, before)
        path = ROOT / f"ansible/roles/{app}/tasks/{file}"
        if not path.is_file():
            continue
        found = from_hook(steps, app, file)
        declares = all(set(t.get("ansible.builtin.set_fact") or {"": 0}) <= set(FLAGS)
                       for t in yaml.safe_load(path.read_text()))
        assert found or declares, f"{app} {file}: no step runs from the hook"
        for where in found:
            assert low < where < high, f"{app} {file}: step {where} is not in ({low}, {high})"
        for other in APPS:
            if other != app:
                assert not from_hook(plans[other], app, file), f"{app} {file} also runs for {other}"
    print(f"ok   {app}: hooks run in place and only for {app}")

FORGET = "Forget the destroyed database backend's generated registry entry"
for app in APPS:
    backend = any("_rm_database_backend" in (t.get("ansible.builtin.set_fact") or {})
                  for t in yaml.safe_load((ROOT / f"ansible/roles/{app}/tasks/remove-inputs.yml")
                                          .read_text()) or []) if (
        ROOT / f"ansible/roles/{app}/tasks/remove-inputs.yml").is_file() else False
    assert any(step.get("name") == FORGET for step in plans[app]) is backend, app
print("ok   only a declared database backend forgets its registry entry")

for app in APPS:
    assert plan_all(ROOT, app, f"{app}-renamed") == plans[app], f"{app}: a renamed instance differs"
print(f"ok   {len(APPS)} apps: a renamed instance resolves to the app's own plan")

playbook = (ROOT / "ansible/playbooks/apps/remove.yml").read_text()
assert "remove_app" not in playbook, "remove_app is never set; use _rm_app_name"
print("ok   remove.yml resolves the app only through _rm_app_name")
