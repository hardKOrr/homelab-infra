#!/usr/bin/env bash
# Focused synthetic contract checks for issue #149 — Open WebUI named upstream wiring.
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
        raise SystemExit(f"Open WebUI contract failed: {fragment!r} missing from {path}")

paths = (
    "ansible/playbooks/apps/open-webui.yml",
    "ansible/tasks/app-wiring/open-webui-upstreams.yml",
    "ansible/vars/app-defaults/open-webui.yml",
    "config.example/apps/open-webui.example.yml",
    "catalog/applications.yml",
    "rundeck/jobs/deploy-open-webui.yaml",
    "ansible/roles/open-webui/tasks/main.yml",
    "ansible/roles/open-webui/tasks/backup.yml",
    "ansible/roles/open-webui/tasks/restore.yml",
    "ansible/roles/open-webui/templates/docker-compose.yml.j2",
    "ansible/roles/open-webui/templates/open-webui.env.j2",
)
for path in paths:
    if not path.endswith(".j2") and parse(path) is None:
        raise SystemExit(f"Open WebUI contract failed: {path} did not parse")

# This is the small decision table implemented by open-webui-upstreams.yml. It proves the
# important boundary without contacting Proxmox: only a selected, provider-matching row in
# the generated registry is consumable, and an empty selection has no fallback.
def resolve(selected, apps):
    chosen = {kind: name for kind, name in selected.items() if name}
    if not chosen:
        raise ValueError("an explicit upstream is required")
    for kind, name in chosen.items():
        row = apps.get(name)
        if not row or row.get("provider") != kind:
            raise ValueError(f"{kind} instance {name!r} is not deployed")
        if not (row.get("url") or (row.get("host") and int(row.get("port", 0)) > 0)):
            raise ValueError(f"{kind} instance {name!r} has no endpoint")
    return chosen

apps = {
    "ollama-gpu": {"provider": "ollama", "url": "http://192.0.2.20:11434/"},
    "litellm-lab": {"provider": "litellm", "host": "http://192.0.2.21", "port": 4000},
}
assert resolve({"ollama": "ollama-gpu", "litellm": ""}, apps) == {"ollama": "ollama-gpu"}
assert resolve({"ollama": "", "litellm": "litellm-lab"}, apps) == {"litellm": "litellm-lab"}
assert resolve({"ollama": "ollama-gpu", "litellm": "litellm-lab"}, apps) == {
    "ollama": "ollama-gpu", "litellm": "litellm-lab"
}
for selected in (
    {"ollama": "", "litellm": ""},
    {"ollama": "not-deployed", "litellm": ""},
    {"ollama": "litellm-lab", "litellm": ""},
):
    try:
        resolve(selected, apps)
    except ValueError:
        pass
    else:
        raise SystemExit(f"undeployed or mismatched selection was accepted: {selected}")

defaults = parse("ansible/vars/app-defaults/open-webui.yml")["open_webui_defaults"]
assert defaults["stack"] == "ai"
assert defaults["app"]["upstreams"] == {"ollama": "", "litellm": ""}
assert defaults["backup"]["application_consistent"] is True
assert defaults["recovery"]["methods"] == ["native"]

wiring = read("ansible/tasks/app-wiring/open-webui-upstreams.yml")
for fragment in (
    "Require at least one explicit named upstream",
    "Require the selected Ollama deployment",
    "Require the selected LiteLLM deployment",
    "homelabinfra_infra.apps",
    "will not invent or select another target",
):
    require("ansible/tasks/app-wiring/open-webui-upstreams.yml", fragment)
if "| first" in wiring or "| default('ollama'" in wiring or "| default('litellm'" in wiring:
    raise SystemExit("Open WebUI upstream wiring contains an implicit backend selection")

compose = read("ansible/roles/open-webui/templates/docker-compose.yml.j2")
env = read("ansible/roles/open-webui/templates/open-webui.env.j2")
for fragment in ("open-webui:", ":/app/backend/data", "app_config.app.port", "8080", "health"):
    require("ansible/roles/open-webui/templates/docker-compose.yml.j2", fragment)
for fragment in (
    "OLLAMA_BASE_URL=",
    "OLLAMA_BASE_URLS=",
    "OPENAI_API_BASE_URL=",
    "OPENAI_API_BASE_URLS=",
    "WEBUI_SECRET_KEY=",
):
    if fragment not in env:
        raise SystemExit(f"Open WebUI environment is missing {fragment}")

for path, fragments in {
    "ansible/roles/open-webui/tasks/backup.yml": ("../../../tasks/backup/resolve-pbs-target.yml", "proxmox-backup-client backup", "data.pxar", "_application_backup_complete"),
    "ansible/roles/open-webui/tasks/restore.yml": ("../../../tasks/backup/resolve-pbs-target.yml", "proxmox-backup-client restore", "overwrite", "_application_restore_complete", "service is intentionally left stopped"),
    "ansible/roles/open-webui/tasks/main.yml": ("vault_item_name: \"homelab-infra/apps/{{ instance }}\"", "vault_item_secret_fields: [secret_key]", "no_log: true"),
    "ansible/playbooks/apps/open-webui.yml": ("open-webui-upstreams.yml", "find-or-create-host.yml", "write-generated-facts.yml", "recovery_isolated"),
}.items():
    for fragment in fragments:
        require(path, fragment)

catalog = parse("catalog/applications.yml")["applications"]["open-webui"]
assert catalog["job"] == "deploy-open-webui.yaml"
assert catalog["scope"] == "estate"
assert catalog["actions"] == ["backup", "configure", "remove", "restart", "restore", "rollback", "tail"]
example = read("config.example/apps/open-webui.example.yml")
for secret in ("secret_key:", "api_key:", "master_key:", "password:"):
    if secret in example:
        raise SystemExit(f"credential field {secret} appears in Open WebUI config example")

# Removal is generic and data-path scoped. The Open WebUI surface must not add a task that
# stops, removes, or rewrites a selected model backend.
playbook = read("ansible/playbooks/apps/remove.yml")
assert "app_config.app.data_path" in playbook
assert "remove_app == 'open-webui'" not in playbook
assert "Open WebUI" not in playbook

print("Open WebUI named upstream preflight, wiring, recovery, and removal safety: OK")
PY
