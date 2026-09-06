#!/usr/bin/env bash
# Focused static contract checks for issue #63 / slice 413.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
need() { grep -Fq -- "$2" "$1" || { echo "missing Odoo contract: $2" >&2; exit 1; }; }

need "$repo/ansible/vars/app-defaults/odoo.yml" 'image: docker.io/odoo:18.0'
need "$repo/ansible/vars/app-defaults/odoo.yml" 'instance: postgresql-odoo'
need "$repo/ansible/vars/app-defaults/odoo.yml" 'version: "16"'
need "$repo/ansible/vars/app-defaults/odoo.yml" 'modules: [crm]'
need "$repo/ansible/roles/odoo/tasks/main.yml" 'vault_item_name: "homelab-infra/apps/{{ instance }}"'
need "$repo/ansible/roles/odoo/tasks/main.yml" 'vault_item_secret_fields: [admin_password, database_password]'
need "$repo/ansible/roles/odoo/tasks/main.yml" 'include_tasks: ../../../tasks/mail/resolve-mail.yml'
need "$repo/ansible/playbooks/apps/odoo.yml" 'tasks/database/provision.yml'
need "$repo/ansible/roles/postgresql/tasks/main.yml" 'postgresql-{{ app_config.app.version }}'
need "$repo/ansible/roles/postgresql/tasks/main.yml" 'Inspect existing clusters before changing the guest'
need "$repo/ansible/roles/postgresql/tasks/main.yml" 'failed_when: false'
need "$repo/ansible/roles/postgresql/tasks/main.yml" 'Normalize existing cluster paths'
need "$repo/ansible/roles/postgresql/tasks/main.yml" "app_config.app.version | default('') == '16'"
need "$repo/ansible/roles/postgresql/tasks/main.yml" "app_config.app.service_name | default('') == 'postgresql@16-main'"
need "$repo/ansible/playbooks/apps/postgresql.yml" 'version: "{{ _postgresql_host.app_config.app.version }}"'
need "$repo/rundeck/jobs/deploy-odoo.yaml" 'Run playbooks/apps/odoo.yml'

echo "PASS: Odoo Community CRM baseline, named PostgreSQL 16 backend, secrets, mail, and surface"
