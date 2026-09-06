#!/usr/bin/env bash
# Focused static contract checks for issue #56 — SearXNG.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
need() { grep -Fq -- "$2" "$1" || { echo "missing SearXNG contract: $2" >&2; exit 1; }; }
absent() { grep -Fq -- "$2" "$1" && { echo "unwanted SearXNG contract: $2" >&2; exit 1; }; return 0; }

need "$repo/ansible/vars/app-defaults/searxng.yml" 'hosting: kubernetes'
need "$repo/ansible/vars/app-defaults/searxng.yml" 'access: internal'
need "$repo/ansible/roles/searxng/tasks/main.yml" 'vault_item_name: "homelab-infra/apps/{{ instance }}"'
need "$repo/ansible/roles/searxng/tasks/main.yml" 'secret_key'
need "$repo/ansible/roles/searxng/templates/settings.yml.j2" 'secret_key: "{{ _sx_secret_key }}"'
need "$repo/ansible/roles/searxng/templates/settings.yml.j2" 'limiter: true'

# No unexamined persistent state: this role must never declare a PersistentVolumeClaim,
# and its app-defaults must never declare a backup contract there is nothing to run.
absent "$repo/ansible/roles/searxng/templates/manifest.yaml.j2" 'kind: PersistentVolumeClaim'

need "$repo/catalog/applications.yml" 'job: deploy-searxng.yaml'
need "$repo/rundeck/jobs/deploy-searxng.yaml" 'Run playbooks/apps/searxng.yml'

echo "PASS: SearXNG internal Kubernetes deploy with no persistent state"
