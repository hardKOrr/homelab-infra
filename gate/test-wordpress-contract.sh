#!/usr/bin/env bash
# Focused synthetic contract checks for issue #60 — WordPress.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
need() { grep -Fq -- "$2" "$1" || { echo "missing WordPress contract: $2 ($1)" >&2; exit 1; }; }
absent() { grep -Fq -- "$2" "$1" && { echo "unwanted WordPress contract: $2 ($1)" >&2; exit 1; } || true; }

for path in \
  "$repo/ansible/playbooks/apps/wordpress.yml" \
  "$repo/ansible/playbooks/maintenance/wordpress-backup.yml" \
  "$repo/ansible/playbooks/maintenance/wordpress-restore.yml" \
  "$repo/ansible/vars/app-defaults/wordpress.yml" \
  "$repo/config.example/apps/wordpress.example.yml" \
  "$repo/ansible/roles/wordpress/tasks/main.yml" \
  "$repo/ansible/roles/wordpress/templates/wordpress.env.j2" \
  "$repo/ansible/roles/wordpress/templates/wordpress-recovery.env.j2" \
  "$repo/ansible/roles/wordpress/templates/docker-compose.yml.j2" \
  "$repo/ansible/roles/wordpress/files/wordpress-recovery" \
  "$repo/rundeck/jobs/deploy-wordpress.yaml" \
  "$repo/rundeck/jobs/wordpress-backup.yaml" \
  "$repo/rundeck/jobs/wordpress-restore.yaml"; do
  [ -f "$path" ] || { echo "missing WordPress file: $path" >&2; exit 1; }
done

need "$repo/ansible/vars/app-defaults/wordpress.yml" 'stack: services'
need "$repo/ansible/vars/app-defaults/wordpress.yml" 'image: wordpress:'
need "$repo/ansible/vars/app-defaults/wordpress.yml" 'cli_image: wordpress:cli-'
need "$repo/ansible/vars/app-defaults/wordpress.yml" 'provider: mariadb'
need "$repo/ansible/vars/app-defaults/wordpress.yml" 'instance: mariadb-wordpress'
need "$repo/ansible/vars/app-defaults/wordpress.yml" 'default_theme: twentytwentyfive'
need "$repo/ansible/vars/app-defaults/wordpress.yml" 'access: public'
need "$repo/ansible/playbooks/apps/wordpress.yml" 'tasks/database/provision.yml'
need "$repo/ansible/playbooks/apps/wordpress.yml" 'tasks/stack/find-or-create-host.yml'
need "$repo/ansible/playbooks/apps/wordpress.yml" 'tasks/backup/resolve-pbs-target.yml'
need "$repo/ansible/roles/wordpress/tasks/main.yml" 'vault_item_name: "homelab-infra/apps/{{ instance }}"'
need "$repo/ansible/roles/wordpress/tasks/main.yml" 'vault_item_secret_fields: [admin_password, database_password]'
need "$repo/ansible/roles/wordpress/tasks/main.yml" 'core'
need "$repo/ansible/roles/wordpress/tasks/main.yml" 'theme'
need "$repo/ansible/roles/wordpress/tasks/main.yml" 'no_log: true'
need "$repo/ansible/roles/wordpress/templates/wordpress.env.j2" 'WORDPRESS_DB_HOST={{ app_config.app.database_host }}'
need "$repo/ansible/roles/wordpress/templates/wordpress.env.j2" 'WORDPRESS_DB_PASSWORD='
need "$repo/ansible/roles/wordpress/templates/docker-compose.yml.j2" ':/var/www/html'
need "$repo/ansible/roles/wordpress/templates/docker-compose.yml.j2" 'wordpress-cli:'
absent "$repo/ansible/roles/wordpress/templates/docker-compose.yml.j2" 'mariadb:'
absent "$repo/ansible/roles/wordpress/templates/docker-compose.yml.j2" 'mysql:'

# One PBS snapshot contains the named database dump and wp-content (uploads/themes/plugins).
need "$repo/ansible/roles/wordpress/files/wordpress-recovery" '--single-transaction'
need "$repo/ansible/roles/wordpress/files/wordpress-recovery" 'database.pxar:'
need "$repo/ansible/roles/wordpress/files/wordpress-recovery" 'content.pxar:'
need "$repo/ansible/roles/wordpress/files/wordpress-recovery" 'WORDPRESS_RESTORE_BACKUP_ID'
need "$repo/ansible/roles/wordpress/files/wordpress-recovery" 'wp_cli option update home'
need "$repo/ansible/roles/wordpress/files/wordpress-recovery" 'wp_cli user update'
need "$repo/ansible/playbooks/maintenance/wordpress-backup.yml" 'wordpress-recovery, --backup'
need "$repo/ansible/playbooks/maintenance/wordpress-restore.yml" 'source_instance'
need "$repo/ansible/playbooks/maintenance/wordpress-restore.yml" 'resolve-existing-host.yml'
need "$repo/ansible/playbooks/maintenance/wordpress-restore.yml" 'overwrite=false'
need "$repo/ansible/playbooks/apps/remove.yml" 'Remove WordPress recovery schedule'
need "$repo/ansible/playbooks/apps/remove.yml" 'Remove WordPress recovery configuration'

need "$repo/catalog/applications.yml" 'job: deploy-wordpress.yaml'
need "$repo/catalog/recovery.yml" 'wordpress: {recovery_issue: 246}'
need "$repo/rundeck/app-actions.yml" 'wordpress_backup:'
need "$repo/rundeck/app-actions.yml" 'wordpress_restore:'
need "$repo/rundeck/jobs/deploy-wordpress.yaml" 'Run playbooks/apps/wordpress.yml'

# Examples are documentation only and must not author generated/backend credentials.
absent "$repo/config.example/apps/wordpress.example.yml" 'admin_password:'
absent "$repo/config.example/apps/wordpress.example.yml" 'database_password:'

python3 - "$repo" <<'PY'
from pathlib import Path
import sys
import yaml

repo = Path(sys.argv[1])
paths = [
    repo / 'ansible/playbooks/apps/wordpress.yml',
    repo / 'ansible/playbooks/maintenance/wordpress-backup.yml',
    repo / 'ansible/playbooks/maintenance/wordpress-restore.yml',
    repo / 'ansible/vars/app-defaults/wordpress.yml',
    repo / 'config.example/apps/wordpress.example.yml',
    repo / 'catalog/applications.yml',
    repo / 'catalog/recovery.yml',
    repo / 'rundeck/app-actions.yml',
]
for path in paths:
    assert yaml.safe_load(path.read_text(encoding='utf-8')) is not None, f'{path} did not parse'

defaults = yaml.safe_load((repo / 'ansible/vars/app-defaults/wordpress.yml').read_text())['wordpress_defaults']
assert defaults['stack'] == 'services'
assert defaults['app']['database'] == {
    'provider': 'mariadb', 'instance': 'mariadb-wordpress', 'name': 'wordpress', 'role': 'wordpress'
}
assert defaults['app']['default_theme'] == 'twentytwentyfive'
assert defaults['routing']['access'] == 'public'
assert defaults['backup']['enabled'] is True
assert defaults['backup']['application_consistent'] is True

catalog = yaml.safe_load((repo / 'catalog/applications.yml').read_text())['applications']['wordpress']
assert catalog == {
    'name': 'WordPress',
    'job': 'deploy-wordpress.yaml',
    'root': 'Applications',
    'category': 'Content & Publishing',
    'type': 'Website',
    'scope': 'estate',
    'extra': ['wordpress_backup', 'wordpress_restore'],
}

tasks = yaml.safe_load((repo / 'ansible/roles/wordpress/tasks/main.yml').read_text())
by_name = {task['name']: task for task in tasks if isinstance(task, dict) and 'name' in task}
assert by_name['WordPress | Render environment file']['no_log'] is True
assert by_name['WordPress | Render Compose project']['no_log'] is True
assert by_name['WordPress | Store application and database credentials in Vaultwarden']['no_log'] is True
assert by_name['WordPress | Initialize the administrator without printing credentials']['no_log'] is True
assert by_name['WordPress | Write recovery configuration']['no_log'] is True
fields = by_name['WordPress | Store application and database credentials in Vaultwarden']['vars']['vault_item_fields']
assert 'database_password' in fields and 'admin_password' in fields
assert 'data_path' not in fields

compose = (repo / 'ansible/roles/wordpress/templates/docker-compose.yml.j2').read_text()
assert compose.count('services:') == 1
assert ':/var/www/html' in compose
assert 'mariadb:' not in compose and 'mysql:' not in compose

helper = (repo / 'ansible/roles/wordpress/files/wordpress-recovery').read_text()
assert 'content.pxar:"$WORDPRESS_CONTENT_PATH"' in helper
assert 'database.pxar:"$work/database"' in helper
assert 'host/$restore_backup_id/*' in helper
assert 'find "$WORDPRESS_CONTENT_PATH"' in helper

restore_job = (repo / 'rundeck/jobs/wordpress-restore.yaml').read_text()
assert 'RD_OPTION_SOURCE_INSTANCE' in restore_job
assert 'source_backup_id' in restore_job
assert 'overwrite=${RD_OPTION_OVERWRITE}' in restore_job

print('WordPress public site, named MariaDB, admin/theme setup, paired recovery, and removal path: OK')
PY

echo "PASS: WordPress deployment and application-consistent recovery surface"
