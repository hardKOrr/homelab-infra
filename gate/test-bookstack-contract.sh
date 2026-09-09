#!/usr/bin/env bash
# Focused synthetic contract checks for issue #141 — BookStack.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
need() { grep -Fq -- "$2" "$1" || { echo "missing BookStack contract: $2 ($1)" >&2; exit 1; }; }
absent() { grep -Fq -- "$2" "$1" && { echo "unwanted BookStack contract: $2 ($1)" >&2; exit 1; } || true; }

for path in \
  "$repo/ansible/playbooks/apps/bookstack.yml" \
  "$repo/ansible/playbooks/maintenance/bookstack-backup.yml" \
  "$repo/ansible/playbooks/maintenance/bookstack-restore.yml" \
  "$repo/ansible/vars/app-defaults/bookstack.yml" \
  "$repo/config.example/apps/bookstack.example.yml" \
  "$repo/ansible/roles/bookstack/tasks/main.yml" \
  "$repo/ansible/roles/bookstack/templates/bookstack.env.j2" \
  "$repo/ansible/roles/bookstack/templates/bookstack-recovery.env.j2" \
  "$repo/ansible/roles/bookstack/templates/docker-compose.yml.j2" \
  "$repo/ansible/roles/bookstack/files/bookstack-recovery" \
  "$repo/rundeck/jobs/deploy-bookstack.yaml" \
  "$repo/rundeck/jobs/bookstack-backup.yaml" \
  "$repo/rundeck/jobs/bookstack-restore.yaml"; do
  [ -f "$path" ] || { echo "missing BookStack file: $path" >&2; exit 1; }
done

need "$repo/ansible/vars/app-defaults/bookstack.yml" 'stack: services'
need "$repo/ansible/vars/app-defaults/bookstack.yml" 'image: lscr.io/linuxserver/bookstack:version-v26.05.3'
need "$repo/ansible/vars/app-defaults/bookstack.yml" 'provider: mariadb'
need "$repo/ansible/vars/app-defaults/bookstack.yml" 'instance: mariadb-bookstack'
need "$repo/ansible/vars/app-defaults/bookstack.yml" 'repository: "http://download.proxmox.com/debian/pbs-client"'
need "$repo/ansible/vars/app-defaults/bookstack.yml" 'data_path: "/opt/{{ instance }}/data"'
need "$repo/ansible/playbooks/apps/bookstack.yml" 'tasks/database/provision.yml'
need "$repo/ansible/playbooks/apps/bookstack.yml" 'tasks/stack/find-or-create-host.yml'
need "$repo/ansible/playbooks/apps/bookstack.yml" 'tasks/backup/resolve-pbs-target.yml'
[ -f "$repo/ansible/tasks/backup/resolve-pbs-target.yml" ] || { echo 'missing shared PBS resolver' >&2; exit 1; }
need "$repo/ansible/roles/bookstack/tasks/main.yml" 'vault_item_name: "homelab-infra/apps/{{ instance }}"'
need "$repo/ansible/roles/bookstack/tasks/main.yml" 'vault_item_secret_fields: [admin_password, app_key, database_password]'
need "$repo/ansible/roles/bookstack/tasks/main.yml" 'bookstack:create-admin'
need "$repo/ansible/roles/bookstack/tasks/main.yml" 'Create recovery configuration directory'
need "$repo/ansible/roles/bookstack/tasks/main.yml" 'Report backup provider no-op'
need "$repo/ansible/roles/bookstack/tasks/main.yml" 'no_log: true'
need "$repo/ansible/roles/bookstack/templates/docker-compose.yml.j2" ':/config'
need "$repo/ansible/roles/bookstack/templates/bookstack.env.j2" 'DB_HOST={{ app_config.app.database_host }}'
need "$repo/ansible/roles/bookstack/templates/bookstack.env.j2" 'MAIL_DRIVER='
absent "$repo/ansible/roles/bookstack/templates/docker-compose.yml.j2" 'mariadb:'
absent "$repo/ansible/roles/bookstack/templates/docker-compose.yml.j2" 'mysql:'

# Backup and restore are one application recovery unit: one PBS snapshot contains both
# the named SQL dump and the durable /config tree. The temporary client credential file
# is mounted for the dump but is outside the archived database directory.
need "$repo/ansible/roles/bookstack/files/bookstack-recovery" '--single-transaction'
need "$repo/ansible/roles/bookstack/files/bookstack-recovery" '--no-create-db'
need "$repo/ansible/roles/bookstack/files/bookstack-recovery" 'database.pxar:"$work/database"'
need "$repo/ansible/roles/bookstack/files/bookstack-recovery" 'storage.pxar:"$BOOKSTACK_DATA_PATH"'
need "$repo/ansible/roles/bookstack/files/bookstack-recovery" '--backup-id "$PBS_BACKUP_ID"'
need "$repo/ansible/roles/bookstack/files/bookstack-recovery" '--restore'
need "$repo/ansible/roles/bookstack/files/bookstack-recovery" 'DROP TABLE IF EXISTS'
absent "$repo/ansible/roles/bookstack/files/bookstack-recovery" 'database.pxar:"$work"'

need "$repo/ansible/playbooks/maintenance/bookstack-backup.yml" 'bookstack-recovery, --backup'
need "$repo/ansible/playbooks/maintenance/bookstack-restore.yml" 'overwrite=false'
need "$repo/ansible/playbooks/maintenance/bookstack-restore.yml" "not (overwrite | default(false) | bool) or snapshot"
need "$repo/ansible/playbooks/maintenance/bookstack-restore.yml" 'resolve-existing-host.yml'
need "$repo/ansible/playbooks/apps/remove.yml" 'Remove BookStack recovery schedule'
need "$repo/ansible/playbooks/apps/remove.yml" 'Remove BookStack recovery configuration'

need "$repo/catalog/applications.yml" 'job: deploy-bookstack.yaml'
need "$repo/rundeck/app-actions.yml" 'bookstack_backup:'
need "$repo/rundeck/app-actions.yml" 'bookstack_restore:'
need "$repo/rundeck/jobs/deploy-bookstack.yaml" 'Run playbooks/apps/bookstack.yml'

# Examples are documentation only and must not author generated/backend credentials.
absent "$repo/config.example/apps/bookstack.example.yml" 'admin_password:'
absent "$repo/config.example/apps/bookstack.example.yml" 'database_password:'
absent "$repo/config.example/apps/bookstack.example.yml" 'app_key:'

python3 - "$repo" <<'PY'
from pathlib import Path
import sys
import yaml

repo = Path(sys.argv[1])
paths = [
    repo / 'ansible/playbooks/apps/bookstack.yml',
    repo / 'ansible/playbooks/maintenance/bookstack-backup.yml',
    repo / 'ansible/playbooks/maintenance/bookstack-restore.yml',
    repo / 'ansible/vars/app-defaults/bookstack.yml',
    repo / 'config.example/apps/bookstack.example.yml',
    repo / 'catalog/applications.yml',
    repo / 'rundeck/app-actions.yml',
]
for path in paths:
    assert yaml.safe_load(path.read_text(encoding='utf-8')) is not None, f'{path} did not parse'

defaults = yaml.safe_load((repo / 'ansible/vars/app-defaults/bookstack.yml').read_text())['bookstack_defaults']
assert defaults['stack'] == 'services'
assert defaults['app']['database']['provider'] in {'mariadb', 'mysql'}
assert defaults['app']['database']['instance'] == 'mariadb-bookstack'
assert defaults['app']['data_path'].endswith('/data')
assert defaults['backup']['enabled'] is True

catalog = yaml.safe_load((repo / 'catalog/applications.yml').read_text())['applications']['bookstack']
assert catalog == {
    'name': 'BookStack',
    'job': 'deploy-bookstack.yaml',
    'root': 'Applications',
    'category': 'Content & Publishing',
    'type': 'Documentation',
    'scope': 'estate',
    'extra': ['bookstack_backup', 'bookstack_restore'],
}

tasks = yaml.safe_load((repo / 'ansible/roles/bookstack/tasks/main.yml').read_text())
rendered = {task['name']: task for task in tasks if isinstance(task, dict) and 'name' in task}
assert rendered['BookStack | Render environment file']['no_log'] is True
assert rendered['BookStack | Render Compose project']['no_log'] is True
assert rendered['BookStack | Store application and database credentials in Vaultwarden']['no_log'] is True
assert rendered['BookStack | Write recovery configuration']['no_log'] is True
assert 'data_path' not in rendered['BookStack | Store application and database credentials in Vaultwarden']['vars']['vault_item_fields']

compose = (repo / 'ansible/roles/bookstack/templates/docker-compose.yml.j2').read_text()
assert compose.count('services:') == 1
assert ':/config' in compose
assert 'mariadb:' not in compose and 'mysql:' not in compose

restore_job = (repo / 'rundeck/jobs/bookstack-restore.yaml').read_text()
assert 'if [ -n "${RD_OPTION_SNAPSHOT:-}" ]; then' in restore_job
assert 'overwrite=${RD_OPTION_OVERWRITE}' in restore_job

print('BookStack catalog, named backend, secret boundary, recovery unit, and removal path: OK')
PY

echo "PASS: BookStack deployment and application-consistent recovery surface"
