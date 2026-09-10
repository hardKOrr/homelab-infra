#!/usr/bin/env bash
# Focused synthetic contract checks for issue #133: Home Assistant Docker-on-VM,
# dedicated USB mapping, Vaultwarden credentials, recovery, and operator surface.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python3 - "$repo" <<'PY'
from pathlib import Path
import sys
import yaml

repo = Path(sys.argv[1])
def read(path):
    return (repo / path).read_text(encoding="utf-8")
def parse(path):
    with (repo / path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)
def require(path, fragment):
    if fragment not in read(path):
        raise SystemExit(f"Home Assistant contract failed: {fragment!r} missing from {path}")
def flatten(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always", "tasks"):
            yield from flatten(task.get(key))

for path in (
    "ansible/playbooks/apps/home-assistant.yml",
    "ansible/vars/app-defaults/home-assistant.yml",
    "config.example/apps/home-assistant.example.yml",
    "catalog/applications.yml",
    "rundeck/jobs/deploy-home-assistant.yaml",
):
    if parse(path) is None:
        raise SystemExit(f"Home Assistant contract failed: {path} did not parse")

defaults = parse("ansible/vars/app-defaults/home-assistant.yml")["home_assistant_defaults"]
if defaults["hosting"] != "docker":
    raise SystemExit("Home Assistant must use the Docker action backend")
if "vm" not in defaults["proxmox"] or defaults["proxmox"]["vm"]["memory"] < 2048:
    raise SystemExit("Home Assistant does not have dedicated VM sizing")
if defaults["app"]["usb_mapping"] != "":
    raise SystemExit("Home Assistant must not invent a USB mapping in defaults")
if not defaults["backup"]["application_consistent"]:
    raise SystemExit("Home Assistant recovery is not application-consistent")

playbook = read("ansible/playbooks/apps/home-assistant.yml")
for fragment in (
    "vm-clone.yml",
    "attach-usb-passthrough.yml",
    "Deploy Home Assistant",
    "record-app-on-guest.yml",
    "combine(_instance_config | default({}), recursive=True)",
):
    require("ansible/playbooks/apps/home-assistant.yml", fragment)
for fragment in ("home-assistant-zigbee", "kind: usb", "mode: dedicated"):
    require("config.example/proxmox.yml", fragment)
if "attach-usb-passthrough-lxc.yml" in playbook or "attach-pci-passthrough.yml" in playbook:
    raise SystemExit("Home Assistant must use dedicated USB VM passthrough only")

role = read("ansible/roles/home-assistant/tasks/main.yml")
for fragment in ("integration_credentials", "vault_item_secret_fields", "no_log: true", "secrets.yaml", "app_config.app.port"):
    require("ansible/roles/home-assistant/tasks/main.yml", fragment)
compose = read("ansible/roles/home-assistant/templates/docker-compose.yml.j2")
for fragment in ("homeassistant:", "devices:", "usb_device_path", "healthcheck"):
    require("ansible/roles/home-assistant/templates/docker-compose.yml.j2", fragment)
if "/dev/bus/usb:/dev/bus/usb" in compose or "privileged: true" in compose:
    raise SystemExit("Home Assistant must not expose the whole USB bus or run privileged")

usb = read("ansible/tasks/proxmox/attach-usb-passthrough.yml")
if usb.index("Assert the physical device is declared dedicated") >= usb.index("Verify the mapping exists"):
    raise SystemExit("USB dedicated-mode preflight must precede mapping inspection")
require("ansible/tasks/proxmox/attach-usb-passthrough.yml", "usb_passthrough_device.mapping")
require("ansible/tasks/proxmox/attach-usb-passthrough.yml", "_usb_device_declarations")

for path, fragments in {
    "ansible/roles/home-assistant/tasks/backup.yml": ("proxmox-backup-client backup", "data.pxar", "_application_backup_complete"),
    "ansible/roles/home-assistant/tasks/restore.yml": ("proxmox-backup-client restore", "overwrite", "_application_restore_complete", "service is intentionally left"),
    "docs/specs/home-assistant-recovery.md": ("dedicated Proxmox USB resource", "integration state", "USB detach"),
}.items():
    for fragment in fragments:
        require(path, fragment)

catalog = parse("catalog/applications.yml")["applications"]["home-assistant"]
if catalog["job"] != "deploy-home-assistant.yaml" or catalog["scope"] != "estate":
    raise SystemExit("Home Assistant catalog identity is incorrect")
if catalog["actions"] != ["backup", "configure", "remove", "restart", "restore", "rollback", "tail"]:
    raise SystemExit("Home Assistant does not expose its complete recovery/action surface")
example = read("config.example/apps/home-assistant.example.yml")
for secret in ("integration_credentials:", "password:", "token:"):
    if secret in example:
        raise SystemExit(f"credential field {secret} appears in Home Assistant config example")
require("ansible/playbooks/apps/remove.yml", "Detach Home Assistant's project-owned Zigbee USB mapping")
require("ansible/playbooks/maintenance/restore-app.yml", "home-assistant")
require("ansible/tasks/maintenance/resolve-app-target.yml", "Docker-on-VM inventory group")

print("Home Assistant Docker-on-VM, dedicated USB authority, secrets, recovery, and operator surface: OK")
PY
