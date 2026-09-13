#!/usr/bin/env bash
# Focused synthetic contract checks for issue #146: Ollama's dedicated PCI GPU,
# large model-storage mount, recovery surface, named endpoint, and exact-device removal.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python3 - "$repo" <<'PY'
from pathlib import Path
import re
import sys
import yaml

repo = Path(sys.argv[1])

def read(path):
    return (repo / path).read_text(encoding="utf-8")

def parse(path):
    with (repo / path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)

def fail(message):
    raise SystemExit(f"Ollama contract failed: {message}")

def require(path, fragment):
    if fragment not in read(path):
        fail(f"{fragment!r} missing from {path}")

paths = (
    "ansible/playbooks/apps/ollama.yml",
    "ansible/vars/app-defaults/ollama.yml",
    "config.example/apps/ollama.example.yml",
    "catalog/applications.yml",
    "rundeck/jobs/deploy-ollama.yaml",
    "ansible/roles/ollama/tasks/main.yml",
    "ansible/roles/ollama/tasks/backup.yml",
    "ansible/roles/ollama/tasks/restore.yml",
    "ansible/roles/ollama/templates/docker-compose.yml.j2",
    "docs/specs/ollama-recovery.md",
)
for path in paths:
    if path.endswith((".yaml", ".yml")) and parse(path) is None:
        fail(f"{path} did not parse")

defaults = parse("ansible/vars/app-defaults/ollama.yml")["ollama_defaults"]
if defaults["hosting"] != "docker":
    fail("Ollama must use Docker on a VM")
if "stack" in defaults:
    fail("Ollama must not land on the shared ai stack")
if defaults["proxmox"]["vm"]["disk_size"] < 512:
    fail("Ollama VM disk is not large enough for local model downloads")
if defaults["app"]["pci_device"] != "":
    fail("Ollama must require an explicit PCI address")
if defaults["app"]["models_path"] != defaults["app"]["storage_path"]:
    fail("Ollama's model mount must be the complete persistent storage tree")
if not defaults["backup"]["application_consistent"]:
    fail("Ollama recovery is not application-consistent")

playbook = read("ansible/playbooks/apps/ollama.yml")
for fragment in (
    "vm-clone.yml",
    "attach-pci-passthrough.yml",
    "pci_passthrough_device",
    "combine(_instance_config | default({}), recursive=True)",
    "Record Ollama endpoint for named AI consumers",
    "generated_facts_service: apps",
    "Deploy Ollama",
    "record-app-on-guest.yml",
    "/api/tags",
):
    require("ansible/playbooks/apps/ollama.yml", fragment)
if "attach-shared-device.yml" in playbook or "stack/find-or-create-host.yml" in playbook:
    fail("Ollama must not use shared LXC hosting or a shared-device seam")

attach = read("ansible/tasks/proxmox/attach-pci-passthrough.yml")
if attach.index("Assert the physical device is declared dedicated") >= attach.index("List every VM on the node"):
    fail("PCI dedicated-mode preflight must precede guest inspection")
for fragment in ("pci_passthrough_device.id", "_.dev+", "preflight failure, not a silent reassignment"):
    require("ansible/tasks/proxmox/attach-pci-passthrough.yml", fragment)
detach = read("ansible/tasks/proxmox/detach-pci-passthrough.yml")
for fragment in ("_.dev+", "Assert the guest carries the ownership tag", "hostpci"):
    require("ansible/tasks/proxmox/detach-pci-passthrough.yml", fragment)
remove = read("ansible/playbooks/apps/remove.yml")
remove_start = remove.index("Detach Ollama's project-owned PCI GPU")
remove_end = remove.index("# A shared render-node bind", remove_start)
remove_branch = remove[remove_start:remove_end]
for fragment in ("detach-pci-passthrough.yml", "(remove_app | default(instance)) == 'ollama'", "pci_passthrough_device"):
    if fragment not in remove_branch:
        fail(f"{fragment!r} missing from Ollama removal branch")
if "detach-shared-device.yml" in remove_branch:
    fail("Ollama removal must use dedicated PCI detach")

compose = read("ansible/roles/ollama/templates/docker-compose.yml.j2")
for fragment in ("ollama:", "11434", "gpus: all", "driver: nvidia", "models_path", "/root/.ollama", "OLLAMA_HOST", "ollama list"):
    require("ansible/roles/ollama/templates/docker-compose.yml.j2", fragment)
if "privileged: true" in compose:
    fail("Ollama must not run privileged")

main_role = read("ansible/roles/ollama/tasks/main.yml")
for fragment in ("nvidia-smi", "docker, info", "regex_escape", "(/|$)", ") is match(", "/api/tags"):
    require("ansible/roles/ollama/tasks/main.yml", fragment)
storage_root = "/opt/ollama/models"
storage_boundary = re.compile(r"^" + re.escape(storage_root) + r"(/|$)")
if not storage_boundary.match("/opt/ollama/models"):
    fail("storage boundary fixture should accept the model mount")
if storage_boundary.match("/opt/ollama/models-other"):
    fail("storage boundary fixture must reject a sibling path")

for path, fragments in {
    "ansible/roles/ollama/tasks/backup.yml": ("models and configuration", "data.pxar:/source", "_application_backup_complete"),
    "ansible/roles/ollama/tasks/restore.yml": ("models and user config", "data.pxar /restore", "find /restore -mindepth 1 -print -quit", "overwrite", "_application_restore_complete", "service stopped"),
    "docs/specs/ollama-recovery.md": ("mode: dedicated", "model-storage", "console recovery", "GPU is hardware"),
}.items():
    for fragment in fragments:
        require(path, fragment)
if "test -d /restore\n" in read("ansible/roles/ollama/tasks/restore.yml"):
    fail("Ollama restore must not treat the bind-mounted staging directory as validation")

catalog = parse("catalog/applications.yml")["applications"]["ollama"]
if catalog["job"] != "deploy-ollama.yaml" or catalog["scope"] != "estate":
    fail("Ollama catalog identity is incorrect")
if catalog["actions"] != ["backup", "configure", "remove", "restart", "restore", "rollback", "tail"]:
    fail("Ollama does not expose its full application action surface")

proxmox_example = read("config.example/proxmox.yml")
for fragment in ("ollama-gpu:", "mode: dedicated", '"0000:02:00.0"'):
    if fragment not in proxmox_example:
        fail(f"{fragment!r} missing from config.example/proxmox.yml")

example = read("config.example/apps/ollama.example.yml")
for fragment in ("pci_device:", "storage_path:", "models_path:", "disk_size: 1024", "application_consistent: true"):
    require("config.example/apps/ollama.example.yml", fragment)
for secret in ("password:", "token:", "api_key:"):
    if secret in example:
        fail(f"credential field {secret} appears in config example")

require("ansible/playbooks/maintenance/restore-app.yml", "Ollama")
print("Ollama dedicated GPU, model storage, endpoint, recovery, removal, and operator surface: OK")
PY
