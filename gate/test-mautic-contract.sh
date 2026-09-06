#!/usr/bin/env bash
# Focused static contract checks for issue #56 — Mautic.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
need() { grep -Fq -- "$2" "$1" || { echo "missing Mautic contract: $2" >&2; exit 1; }; }

need "$repo/ansible/vars/app-defaults/mautic.yml" 'stack: services'
need "$repo/ansible/vars/app-defaults/mautic.yml" 'provider: mariadb'
need "$repo/ansible/vars/app-defaults/mautic.yml" 'instance: mariadb-mautic'
need "$repo/ansible/roles/mautic/tasks/main.yml" 'vault_item_name: "homelab-infra/apps/{{ instance }}"'
need "$repo/ansible/roles/mautic/tasks/main.yml" 'vault_item_secret_fields: [admin_password, database_password]'
need "$repo/ansible/roles/mautic/tasks/main.yml" 'include_tasks: ../../../tasks/mail/resolve-mail.yml'
need "$repo/ansible/roles/mautic/templates/local.php.j2" "wiring_mail.enabled"
need "$repo/ansible/roles/mautic/templates/local.php.j2" "'mailer_transport' => 'smtp'"
need "$repo/ansible/playbooks/apps/mautic.yml" 'tasks/database/provision.yml'
need "$repo/ansible/playbooks/apps/mautic.yml" 'stack/find-or-create-host.yml'

need "$repo/catalog/applications.yml" 'job: deploy-mautic.yaml'
need "$repo/rundeck/jobs/deploy-mautic.yaml" 'Run playbooks/apps/mautic.yml'

echo "PASS: Mautic named MariaDB backend, mail contract, secrets, and surface"
