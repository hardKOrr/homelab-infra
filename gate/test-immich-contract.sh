#!/usr/bin/env bash
# Focused synthetic contract checks for issue #131 — Immich.
#
# These checks exercise the repository-owned mount/device seams and the app surface without
# contacting Proxmox, PBS, PostgreSQL, Redis, Docker, or a live Immich deployment.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python3 - "$repo" <<'PY'
from pathlib import Path
import sys
import yaml

repo = Path(sys.argv[1])
def read(path):
    return (repo / path).read_text(encoding='utf-8')
def parse(path):
    with (repo / path).open(encoding='utf-8') as fh:
        return yaml.safe_load(fh)
def fail(message):
    raise SystemExit(f"Immich contract failed: {message}")

def require(path, fragment):
    if fragment not in read(path):
        fail(f"{fragment!r} missing from {path}")

def flatten(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ('block', 'rescue', 'always', 'tasks'):
            yield from flatten(task.get(key))

# Defaults and deployment surface: one named backend each, pgvector on PostgreSQL, and an
# explicitly empty default mount so a global library can never be adopted accidentally.
defaults = parse('ansible/vars/app-defaults/immich.yml')['immich_defaults']
if defaults['stack'] != 'photos':
    fail('Immich is not assigned to the photos stack')
if defaults['app']['database']['instance'] != 'postgresql-immich':
    fail('Immich PostgreSQL instance is not exact')
if defaults['app']['redis']['instance'] != 'redis-immich':
    fail('Immich Redis instance is not exact')
if defaults['app']['database']['extensions'] != ['vector']:
    fail('Immich does not request the vector extension')
if defaults['media_storage']['mounts'] != []:
    fail('Immich defaults adopt a media mount')
if not defaults['backup']['application_consistent']:
    fail('Immich recovery is not application-consistent')

playbook = read('ansible/playbooks/apps/immich.yml')
for fragment in ('tasks/database/provision.yml', 'attach-host-mounts.yml',
                 'attach-shared-device.yml', 'shared_device_mode', 'stack_name'):
    require('ansible/playbooks/apps/immich.yml', fragment)
if 'include_tasks: ../../tasks/proxmox/attach-pci-passthrough.yml' in playbook:
    fail('Immich requests dedicated PCI passthrough')

# The device declaration gate must precede node inspection and the mount seam must validate
# the exact existing host directory before it mutates the guest.
device_tasks = list(flatten(parse('ansible/tasks/proxmox/attach-shared-device.yml')))
names = [task.get('name', '') for task in device_tasks]
mode = next((i for i, n in enumerate(names) if 'Assert every physical device is declared shared' in n), -1)
node = next((i for i, n in enumerate(names) if "List the node's containers" in n), -1)
if mode < 0 or node < 0 or mode >= node:
    fail('shared device mode preflight does not precede Proxmox inspection')
mount_tasks = list(flatten(parse('ansible/tasks/proxmox/attach-host-mounts.yml')))
mount_names = [task.get('name', '') for task in mount_tasks]
path_check = next((i for i, n in enumerate(mount_names) if 'Verify the host paths exist' in n), -1)
path_assert = next((i for i, n in enumerate(mount_names) if 'Assert every host path is a directory' in n), -1)
if path_check < 0 or path_assert < 0 or path_check >= path_assert:
    fail('mount path ownership preflight is missing or out of order')

compose = read('ansible/roles/immich/templates/docker-compose.yml.j2')
for forbidden in ('immich-postgres:', 'immich-redis:', 'postgres:', 'redis:'):
    if forbidden in compose:
        fail(f'Compose contains forbidden backend sidecar {forbidden}')
for fragment in ('immich-server:', 'immich-machine-learning:', 'devices:',
                 'app_config.app.shared_device', ':/data'):
    require('ansible/roles/immich/templates/docker-compose.yml.j2', fragment)

for path, fragments in {
    'ansible/roles/immich/tasks/backup.yml': ('pg_dump', 'database.pxar', 'media.pxar',
                                              '_application_backup_complete'),
    'ansible/roles/immich/tasks/restore.yml': ('pg_restore', 'database.pxar', 'media.pxar',
                                               '_application_restore_complete',
                                               'left stopped'),
}.items():
    for fragment in fragments:
        require(path, fragment)

example = read('config.example/apps/immich.example.yml')
for fragment in ('media_storage:', 'host: /srv/photos/immich', 'path: /mnt/photos/immich',
                 'shared_device: /dev/dri/renderD128', 'mode: shared', 'backup:'):
    require('config.example/apps/immich.example.yml', fragment)
for secret in ('database_password:', 'redis_password:', 'password:'):
    if secret in example:
        fail(f'credential field {secret} appears in config example')

catalog = parse('catalog/applications.yml')['applications']['immich']
if catalog['actions'] != ['backup', 'configure', 'remove', 'restart', 'restore', 'rollback', 'tail']:
    fail('Immich does not expose its full explicit action surface')
if catalog['scope'] != 'estate' or catalog['job'] != 'deploy-immich.yaml':
    fail('Immich catalog identity is incorrect')
require('rundeck/jobs/deploy-immich.yaml', 'playbooks/apps/immich.yml')
require('ansible/playbooks/apps/remove.yml', 'detach-shared-device.yml')
if "remove_app == 'immich'" in read('ansible/playbooks/apps/remove.yml'):
    fail('shared-device removal is hardcoded to Immich')
require('ansible/playbooks/apps/remove.yml', "app_config.app.shared_device | default('') | length > 0")
require('ansible/tasks/database/provision.yml', 'postgresql-{{ database_provision_config.version')
require('ansible/tasks/database/provision.yml', 'CREATE EXTENSION IF NOT EXISTS vector')

print('PASS: Immich named backends, durable mount ownership, shared iGPU gate, recovery, and full operator surface')
PY
