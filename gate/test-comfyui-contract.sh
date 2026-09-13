#!/usr/bin/env bash
# Focused synthetic contract checks for issue #147: ComfyUI's dedicated PCI GPU,
# large model-storage tree, recovery surface, and exact-device removal.
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
def fail(message):
    raise SystemExit(f"ComfyUI contract failed: {message}")
def require(path, fragment):
    if fragment not in read(path):
        fail(f"{fragment!r} missing from {path}")

paths = (
    "ansible/playbooks/apps/comfyui.yml",
    "ansible/vars/app-defaults/comfyui.yml",
    "config.example/apps/comfyui.example.yml",
    "catalog/applications.yml",
    "rundeck/jobs/deploy-comfyui.yaml",
)
for path in paths:
    if parse(path) is None:
        fail(f"{path} did not parse")

defaults = parse("ansible/vars/app-defaults/comfyui.yml")["comfyui_defaults"]
if defaults["hosting"] != "docker":
    fail("ComfyUI must use Docker on a VM")
if "stack" in defaults:
    fail("ComfyUI must not land on the shared ai stack")
if defaults["proxmox"]["vm"]["disk_size"] < 256:
    fail("ComfyUI VM disk is not large enough for model downloads")
if defaults["app"]["pci_device"] != "":
    fail("ComfyUI must require an explicit PCI address")
for key in ("storage_path", "models_path", "config_path", "custom_nodes_path", "input_path", "output_path"):
    if not defaults["app"][key].startswith("/opt/{{ instance }}/storage"):
        fail(f"{key} is outside the application storage tree")
if not defaults["backup"]["application_consistent"]:
    fail("ComfyUI recovery is not application-consistent")

playbook = read("ansible/playbooks/apps/comfyui.yml")
for fragment in (
    "vm-clone.yml",
    "attach-pci-passthrough.yml",
    "pci_passthrough_device",
    "combine(_instance_config | default({}), recursive=True)",
    "Deploy ComfyUI",
    "record-app-on-guest.yml",
):
    require("ansible/playbooks/apps/comfyui.yml", fragment)
if "attach-shared-device.yml" in playbook or "stack/find-or-create-host.yml" in playbook:
    fail("ComfyUI must not use shared LXC hosting or a shared-device seam")

attach = read("ansible/tasks/proxmox/attach-pci-passthrough.yml")
if attach.index("Assert the physical device is declared dedicated") >= attach.index("List every VM on the node"):
    fail("PCI dedicated-mode preflight must precede guest inspection")
for fragment in ("pci_passthrough_device.id", "_.dev+", "preflight failure, not a silent reassignment"):
    require("ansible/tasks/proxmox/attach-pci-passthrough.yml", fragment)
detach = read("ansible/tasks/proxmox/detach-pci-passthrough.yml")
for fragment in ("_.dev+", "Assert the guest carries the ownership tag", "hostpci"):
    require("ansible/tasks/proxmox/detach-pci-passthrough.yml", fragment)

compose = read("ansible/roles/comfyui/templates/docker-compose.yml.j2")
for fragment in ("comfyui:", "gpus: all", "driver: nvidia", "models_path", "config_path", "/system_stats"):
    require("ansible/roles/comfyui/templates/docker-compose.yml.j2", fragment)
if "privileged: true" in compose:
    fail("ComfyUI must not run privileged")

for path, fragments in {
    "ansible/roles/comfyui/tasks/backup.yml": ("models and configuration", "data.pxar:/source", "_application_backup_complete"),
    "ansible/roles/comfyui/tasks/restore.yml": ("models and user config", "data.pxar /restore", "overwrite", "_application_restore_complete", "service stopped"),
    "docs/specs/comfyui-recovery.md": ("mode: dedicated", "model-storage", "console recovery", "GPU is hardware"),
}.items():
    for fragment in fragments:
        require(path, fragment)

catalog = parse("catalog/applications.yml")["applications"]["comfyui"]
if catalog["job"] != "deploy-comfyui.yaml" or catalog["scope"] != "estate":
    fail("ComfyUI catalog identity is incorrect")
if catalog["actions"] != ["backup", "configure", "remove", "restart", "restore", "rollback", "tail"]:
    fail("ComfyUI does not expose its full application action surface")
require("ansible/playbooks/apps/remove.yml", "Detach ComfyUI's project-owned PCI GPU")
proxmox_example = read("config.example/proxmox.yml")
for fragment in ("comfyui-gpu:", "mode: dedicated", "0000:01:00.0"):
    if fragment not in proxmox_example:
        fail(f"{fragment!r} missing from config.example/proxmox.yml")
remove = read("ansible/playbooks/apps/remove.yml")
if "detach-shared-device.yml" in remove[remove.index("Detach ComfyUI's project-owned PCI GPU"):remove.index("Detach ComfyUI's project-owned PCI GPU") + 700]:
    fail("ComfyUI removal must use dedicated PCI detach")
require("ansible/playbooks/maintenance/restore-app.yml", "ComfyUI")

example = read("config.example/apps/comfyui.example.yml")
for fragment in ("pci_device:", "storage_path:", "models_path:", "disk_size: 512", "application_consistent: true"):
    require("config.example/apps/comfyui.example.yml", fragment)
for secret in ("password:", "token:", "api_key:"):
    if secret in example:
        fail(f"credential field {secret} appears in config example")

print("ComfyUI dedicated GPU, model storage, recovery, removal, and operator surface: OK")
PY
