#!/usr/bin/env bash
# Focused static contract checks for issue #56 — Hi.Events.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
need() { grep -Fq -- "$2" "$1" || { echo "missing Hi.Events contract: $2" >&2; exit 1; }; }

need "$repo/ansible/vars/app-defaults/hi-events.yml" 'hosting: kubernetes'
need "$repo/ansible/vars/app-defaults/hi-events.yml" 'provider: postgresql'
need "$repo/ansible/vars/app-defaults/hi-events.yml" 'instance: postgresql-hi-events'
need "$repo/ansible/vars/app-defaults/hi-events.yml" 'access: public'
need "$repo/ansible/roles/hi-events/tasks/main.yml" 'include_tasks: ../../../tasks/mail/resolve-mail.yml'
need "$repo/ansible/roles/hi-events/tasks/main.yml" 'wiring_mail.enabled | default(false) | bool'
need "$repo/ansible/roles/hi-events/tasks/main.yml" 'vault_item_name: "homelab-infra/apps/{{ instance }}"'
need "$repo/ansible/playbooks/apps/hi-events.yml" 'tasks/database/provision.yml'
need "$repo/ansible/playbooks/apps/hi-events.yml" "wiring_access: \"{{ hostvars['localhost'].app_config.routing.access | default('public') }}\""
need "$repo/ansible/roles/hi-events/tasks/restore.yml" "{{ restore_target }}-storage"
need "$repo/ansible/roles/hi-events/templates/restore-job.yaml.j2" '/app-storage'

# Published image (not a nonexistent ghcr.io path), the real Laravel storage root this
# image ships (/app/backend, not /var/www/html), and the production env shape its own
# docker-compose.yml actually reads (DATABASE_URL, JWT_SECRET, VITE_*) rather than a
# DB_HOST-style split this image does not consume.
need "$repo/ansible/vars/app-defaults/hi-events.yml" 'daveearley/hi.events-all-in-one'
need "$repo/ansible/roles/hi-events/templates/manifest.yaml.j2" 'mountPath: /app/backend/storage'
need "$repo/ansible/roles/hi-events/tasks/main.yml" '_he_jwt_secret'
need "$repo/ansible/roles/hi-events/tasks/main.yml" 'JWT_SECRET: "{{ _he_jwt_secret }}"'
need "$repo/ansible/roles/hi-events/tasks/main.yml" 'DATABASE_URL: "{{ _he_database_url }}"'
need "$repo/ansible/roles/hi-events/tasks/main.yml" 'VITE_FRONTEND_URL'
need "$repo/ansible/roles/hi-events/tasks/main.yml" 'VITE_API_URL_CLIENT'

need "$repo/catalog/applications.yml" 'job: deploy-hi-events.yaml'
need "$repo/rundeck/jobs/deploy-hi-events.yaml" 'Run playbooks/apps/hi-events.yml'

echo "PASS: Hi.Events named PostgreSQL backend, mail contract, and public access class"
