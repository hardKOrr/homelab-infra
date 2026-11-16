#!/usr/bin/env python3
"""Remove App stops the units a native deploy installed, whatever the instance is named.

Without a remove-units.yml hook, Remove stops `app.service_name`, else a unit named after
the instance. So every systemd unit file a native role writes, after its role defaults
are expanded, must be named `{{ instance }}` or `{{ app_config.app.service_name }}`
(declared in vars/app-defaults/<app>.yml), or the role must have the hook."""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
APPS = list(yaml.safe_load((ROOT / "catalog/applications.yml").read_text())["applications"])
ALIASES = yaml.safe_load((ROOT / "gate/structure.yml").read_text())["role_aliases"]
UNIT = re.compile(r"dest:\s*\"?/etc/systemd/system/(.+?)\.service\"?\s*$", re.M)
VAR = re.compile(r"^\{\{\s*([a-z0-9_]+)\s*\}\}$")
ACCEPTED = {"{{ instance }}", "{{ app_config.app.service_name }}"}

failures = []
for app in APPS:
    defaults = ROOT / f"ansible/vars/app-defaults/{app}.yml"
    if not defaults.is_file():
        continue
    config = next(iter((yaml.safe_load(defaults.read_text()) or {None: {}}).values())) or {}
    if "native" not in str((config.get("hosting") or {}).get("supported", [])):
        continue
    role = ALIASES.get(app, app)
    if role is None:
        continue
    role_dir = ROOT / "ansible/roles" / role
    if (ROOT / f"ansible/roles/{app}/tasks/remove-units.yml").is_file():
        continue
    role_defaults = {}
    if (role_dir / "defaults/main.yml").is_file():
        role_defaults = yaml.safe_load((role_dir / "defaults/main.yml").read_text()) or {}
    service_name = (config.get("app") or {}).get("service_name")
    for task_file in sorted((role_dir / "tasks").rglob("*.yml")):
        for unit in UNIT.findall(task_file.read_text()):
            name = unit.strip()
            match = VAR.match(name)
            if match and isinstance(role_defaults.get(match.group(1)), str):
                name = role_defaults[match.group(1)].strip()
            if name in ACCEPTED or (service_name and name == service_name):
                continue
            failures.append(f"{app}: {task_file.relative_to(ROOT)} writes unit {unit!r}, which "
                            "Remove does not stop for a renamed instance; declare app.service_name "
                            "or add roles/<app>/tasks/remove-units.yml")
    print(f"ok   {app}: native units resolve for any instance name" if not any(
        f.startswith(f"{app}:") for f in failures) else f"FAIL {app}")

if failures:
    print("\n".join(failures), file=sys.stderr)
    sys.exit(1)
