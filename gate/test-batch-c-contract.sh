#!/usr/bin/env bash
# Focused synthetic wiring checks for issue #54 — Batch C applications.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
need() { grep -Fq -- "$2" "$1" || { echo "missing Batch C contract: $2 ($1)" >&2; exit 1; }; }
absent() { grep -Fq -- "$2" "$1" && { echo "unwanted Batch C contract: $2 ($1)" >&2; exit 1; } || true; }

for app in n8n plane forgejo forgejo-runner; do
  need "$repo/ansible/vars/app-defaults/$app.yml" 'stack: services'
  need "$repo/ansible/playbooks/apps/$app.yml" 'stack/find-or-create-host.yml'
  need "$repo/ansible/playbooks/apps/$app.yml" "combine({'app':"
  need "$repo/config.example/apps/$app.example.yml" 'backup:'
  need "$repo/rundeck/jobs/deploy-$app.yaml" "playbooks/apps/$app.yml"
done

need "$repo/ansible/vars/app-defaults/n8n.yml" 'provider: postgresql'
need "$repo/ansible/vars/app-defaults/n8n.yml" 'instance: postgresql-n8n'
need "$repo/ansible/playbooks/apps/n8n.yml" 'tasks/database/provision.yml'
need "$repo/ansible/roles/n8n/tasks/main.yml" 'vault_item_secret_fields: [encryption_key, database_password]'
need "$repo/ansible/roles/n8n/tasks/main.yml" 'no_log: true'
need "$repo/ansible/roles/n8n/templates/n8n.env.j2" 'N8N_ENCRYPTION_KEY='
need "$repo/ansible/roles/n8n/templates/n8n.env.j2" 'DB_POSTGRESDB_PASSWORD='
need "$repo/ansible/roles/n8n/templates/docker-compose.yml.j2" 'env_file:'
need "$repo/ansible/roles/n8n/templates/docker-compose.yml.j2" 'healthz'

need "$repo/ansible/vars/app-defaults/plane.yml" 'instance: postgresql-plane'
need "$repo/ansible/vars/app-defaults/plane.yml" 'instance: redis-plane'
need "$repo/ansible/vars/app-defaults/plane.yml" 'instance: plane-minio'
need "$repo/ansible/roles/plane/tasks/main.yml" '_plane_redis_password'
need "$repo/ansible/roles/plane/tasks/main.yml" 'vault_item_secret_fields:'
need "$repo/ansible/roles/plane/tasks/main.yml" 'docker-compose.yml.j2'
need "$repo/ansible/roles/plane/tasks/main.yml" 'plane-migrator'
need "$repo/ansible/roles/plane/templates/docker-compose.yml.j2" 'plane-web:'
need "$repo/ansible/roles/plane/templates/docker-compose.yml.j2" 'plane-worker:'
need "$repo/ansible/roles/plane/templates/docker-compose.yml.j2" 'plane-beat-worker:'
need "$repo/ansible/roles/plane/templates/docker-compose.yml.j2" 'plane-minio:'
need "$repo/ansible/roles/plane/templates/docker-compose.yml.j2" 'plane-minio-init:'
absent "$repo/ansible/roles/plane/templates/docker-compose.yml.j2" 'plane-db:'
absent "$repo/ansible/roles/plane/templates/docker-compose.yml.j2" 'plane-redis:'

need "$repo/ansible/vars/app-defaults/forgejo.yml" 'ssh_port: 2222'
need "$repo/ansible/roles/forgejo/templates/docker-compose.yml.j2" '"{{ app_config.app.port }}:3000"'
need "$repo/ansible/roles/forgejo/templates/docker-compose.yml.j2" '"{{ app_config.app.ssh_port }}:2222"'
need "$repo/ansible/roles/forgejo/templates/docker-compose.yml.j2" 'Caddy or any HTTP reverse-proxy route'
need "$repo/ansible/roles/forgejo/templates/forgejo.env.j2" 'FORGEJO__server__START_SSH_SERVER=true'
need "$repo/ansible/playbooks/apps/forgejo.yml" 'generated_facts_service: apps'
need "$repo/ansible/playbooks/apps/forgejo.yml" 'Wire reverse proxy (HTTP only)'

need "$repo/ansible/vars/app-defaults/forgejo-runner.yml" 'instance: forgejo'
need "$repo/ansible/roles/forgejo-runner/tasks/main.yml" '_forgejo_runner_token'
need "$repo/ansible/roles/forgejo-runner/files/register-and-run.sh" 'forgejo-runner register'
need "$repo/ansible/roles/forgejo-runner/files/register-and-run.sh" 'homelab-infra-registered'
need "$repo/ansible/roles/forgejo-runner/templates/docker-compose.yml.j2" 'docker:27-dind'
need "$repo/ansible/playbooks/apps/forgejo-runner.yml" 'Deploy that named Forgejo instance before deploying'
need "$repo/ansible/playbooks/apps/forgejo-runner.yml" 'homelabinfra_infra.apps'
need "$repo/ansible/tasks/app-wiring/forgejo-runner-remove.yml" 'method: DELETE'
need "$repo/ansible/tasks/app-wiring/forgejo-runner-remove.yml" 'admin_api_token'
need "$repo/ansible/playbooks/apps/remove.yml" 'Deregister Forgejo Runner'
need "$repo/ansible/playbooks/apps/remove.yml" 'Remove Forgejo Runner registration state'
need "$repo/ansible/scripts/registry-forget.py" 'if key == "apps":'

# These are the three sensitive values that must never be authored in a tracked app
# example. The comments may document their Vaultwarden field names, but no YAML key/value
# enables them from config.example.
absent "$repo/config.example/apps/n8n.example.yml" 'credentials:'
absent "$repo/config.example/apps/plane.example.yml" 'object_storage:'
absent "$repo/config.example/apps/forgejo-runner.example.yml" 'runner_token:'

python3 - "$repo" <<'PY'
import pathlib, sys, yaml
repo = pathlib.Path(sys.argv[1])
for app in ('n8n', 'plane', 'forgejo', 'forgejo-runner'):
    for path in (
        repo / 'ansible' / 'playbooks' / 'apps' / f'{app}.yml',
        repo / 'ansible' / 'vars' / 'app-defaults' / f'{app}.yml',
        repo / 'config.example' / 'apps' / f'{app}.example.yml',
    ):
        with path.open(encoding='utf-8') as handle:
            assert yaml.safe_load(handle) is not None, f'{path} did not parse'

catalog = yaml.safe_load((repo / 'catalog' / 'applications.yml').read_text())
apps = catalog['applications']
for slug in ('n8n', 'plane', 'forgejo', 'forgejo-runner'):
    entry = apps[slug]
    assert entry['job'] == f'deploy-{slug}.yaml'
    assert entry['scope'] == 'estate'

# Prove the one dangerous publication is present exactly as two host mappings and does
# not acquire a reverse-proxy SSH route.
compose = (repo / 'ansible' / 'roles' / 'forgejo' / 'templates' / 'docker-compose.yml.j2').read_text()
assert compose.count('ports:') == 1
assert 'app_config.app.ssh_port }}:2222' in compose
assert 'wiring_ssh' not in (repo / 'ansible' / 'playbooks' / 'apps' / 'forgejo.yml').read_text()
print('Batch C app catalog, secret boundaries, dependencies, and Forgejo publication: OK')
PY

echo "PASS: Batch C n8n, Plane, Forgejo, and Forgejo Runner surface"
