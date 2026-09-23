#!/usr/bin/env bash
# Focused synthetic contract checks for issue #145 — LiteLLM.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
need() { grep -Fq -- "$2" "$1" || { echo "missing LiteLLM contract: $2 ($1)" >&2; exit 1; }; }
absent() { grep -Fq -- "$2" "$1" && { echo "unwanted LiteLLM contract: $2 ($1)" >&2; exit 1; } || true; }

defaults="$repo/ansible/vars/app-defaults/litellm.yml"
role="$repo/ansible/roles/litellm"
playbook="$repo/ansible/playbooks/apps/litellm.yml"
example="$repo/config.example/apps/litellm.example.yml"

need "$defaults" 'hosting: kubernetes'
need "$defaults" 'provider: postgresql'
need "$defaults" 'instance: postgresql-litellm'
need "$defaults" 'methods: [native]'
need "$role/templates/config.yaml.j2" 'store_model_in_db: false'
need "$role/tasks/main.yml" 'vault_item_name: "homelab-infra/apps/{{ instance }}"'
need "$role/tasks/main.yml" 'k8s_secret_name: "{{ instance }}-runtime"'
need "$role/tasks/main.yml" '_litellm_database_url: "postgresql://{{ app_config.app.database_user }}:{{ app_config.app.database_password }}@{{ app_config.app.database_host }}:{{ app_config.app.database_port }}/{{ app_config.app.database_name }}"'
need "$role/tasks/main.yml" 'app_config.app.credentials'
need "$role/tasks/main.yml" 'Reject inline provider credentials'
need "$role/tasks/main.yml" 'backup-cronjob.yaml.j2'
need "$role/templates/restore-job.yaml.j2" 'pg_restore --clean'
need "$role/tasks/restore.yml" 'ROUTING_CONFIG_B64'
need "$role/templates/manifest.yaml.j2" '/health/readiness'
need "$role/templates/manifest.yaml.j2" '/health/liveliness'
need "$role/templates/backup-cronjob.yaml.j2" 'pg_dump --format=custom'
need "$role/templates/backup-cronjob.yaml.j2" 'config.pxar:/app-config'
need "$role/templates/restore-job.yaml.j2" 'config.pxar /restore/config'
need "$playbook" 'tasks/database/provision.yml'
need "$playbook" 'provider: litellm'
need "$example" 'OPENAI_API_KEY: openai_api_key'
need "$repo/catalog/applications.yml" 'job: deploy-litellm.yaml'
need "$repo/rundeck/jobs/deploy-litellm.yaml" 'Run playbooks/apps/litellm.yml'

# No application PVC: the hosting decision is the external PostgreSQL + Vaultwarden path,
# not a node-pinned Kubernetes volume.
absent "$role/templates/manifest.yaml.j2" 'kind: PersistentVolumeClaim'
# Provider values must not be authored in the example or generated facts surface.
absent "$example" 'sk-'
absent "$example" 'api_key:'

python3 - "$defaults" "$example" "$repo/catalog/applications.yml" <<'PY'
import sys
import yaml

defaults = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["litellm_defaults"]
example = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))
catalog = yaml.safe_load(open(sys.argv[3], encoding="utf-8"))["applications"]["litellm"]
assert defaults["hosting"] == "kubernetes"
assert defaults["app"]["database"]["provider"] == "postgresql"
assert defaults["recovery"]["methods"] == ["native"]
assert example["app"]["credentials"]["OPENAI_API_KEY"] == "openai_api_key"
assert catalog["scope"] == "estate"
assert set(catalog["actions"]) >= {"backup", "configure", "remove", "restore"}
print("LiteLLM defaults, secret boundary, external database and catalog: OK")
PY

echo "PASS: LiteLLM Kubernetes external-state, backup/restore and operator surface"
