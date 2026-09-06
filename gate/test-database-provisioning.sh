#!/bin/bash
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
task="$repo/ansible/tasks/database/provision.yml"

need() { grep -Fq -- "$1" "$task" || { echo "missing database contract: $1" >&2; exit 1; }; }
need "['postgresql', 'mariadb', 'mysql']"
need "credential_action | default('reuse') in ['reuse', 'rotate']"
need 'vault_item_name: "homelab-infra/apps/{{ database_provision_app_instance }}"'
need "vault_item_secret_fields: [database_password]"
need "_database_existing_password | length == 0 or _database_credential_action == 'rotate'"
need "update_password:"
need 'priv: "{{ database_provision_config.name }}.*:ALL"'
need "_database_backend.client_hosts | default([]) | length > 0"
need "no_log: true"

echo "PASS: PostgreSQL, MariaDB, and MySQL provisioning contract positive/negative guards"
