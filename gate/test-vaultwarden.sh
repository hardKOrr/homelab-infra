#!/usr/bin/env bash
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
work="$(mktemp -d "${TMPDIR:-/tmp}/homelab-vault-test.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT

fail() { echo "vault test failed: $*" >&2; exit 1; }

# Canonical item mapping, including application and estate scopes.
printf '%s' '[
 {"name":"homelab-infra/proxmox","fields":[{"name":"api_token_secret","value":"pve-value"}]},
 {"name":"homelab-infra/runner","fields":[{"name":"ssh_private_key","value":"ssh-value"}]},
 {"name":"homelab-infra/vaultwarden","fields":[{"name":"admin_token","value":"admin-value"}]},
 {"name":"homelab-infra/reverse_proxy","fields":[{"name":"dns_api_token","value":"dns-edit-value"}]},
 {"name":"homelab-infra/media/sonarr","fields":[{"name":"api_key","value":"media-value"}]},
 {"name":"homelab-infra/apps/wiki","fields":[{"name":"oidc_client_secret","value":"oidc-value"}]},
 {"name":"homelab-infra/estates/home/dns","fields":[{"name":"api_key","value":"dns-value"}]}
]' | python3 "$repo/ansible/scripts/vault-runtime.py" \
  --require homelab-infra/proxmox > "$work/runtime.json"
python3 - "$work/runtime.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
assert d["media"]["sonarr"]["api_key"] == "media-value"
assert d["apps"]["wiki"]["oidc_client_secret"] == "oidc-value"
assert d["estates"]["home"]["dns"]["api_key"] == "dns-value"
assert d["reverse_proxy"]["dns_api_token"] == "dns-edit-value"
PY

# The existing Caddy deploy job accepts optional Seed credentials before enrollment
# and the same job accepts Vaultwarden credentials after cutover.
python3 "$repo/rundeck/render-job.py" \
  "$repo/rundeck/jobs/deploy-caddy.yaml" > "$work/caddy-job.yml"
grep -q 'storagePath: keys/project/homelab-infra/bootstrap/cloudflare-api-token' \
  "$work/caddy-job.yml" || fail "Deploy Caddy has no encrypted Cloudflare Seed input"
grep -q 'storagePath: keys/project/homelab-infra/vaultwarden-machine/client-id' \
  "$work/caddy-job.yml" || fail "Deploy Caddy has no Vault-mode credential input"
python3 - "$work/caddy-job.yml" <<'PY'
import sys, yaml
job = yaml.safe_load(open(sys.argv[1]))[0]
options = {item["name"]: item for item in job["options"]}
for name in ("cloudflare_api_token", "bw_clientid", "bw_clientsecret", "bw_password"):
    assert options[name]["required"] is False, name
PY

# A post-cutover bootstrap re-run must not replace the platform SSH identity. Vault mode
# intentionally leaves only the public half on disk; the private half is materialized from
# Vaultwarden for each job by lab-run.sh.
grep -Fq '[ -f "$LAB_ETC/state/vault-mode" ] && [ -f "${LAB_SSH_KEY}.pub" ]' \
  "$repo/rundeck/bootstrap-rundeck.sh" \
  || fail "bootstrap does not recognize the post-cutover SSH-key state"
grep -Fq 'platform SSH private key is held in Vaultwarden' \
  "$repo/rundeck/bootstrap-rundeck.sh" \
  || fail "bootstrap can rotate the post-cutover platform SSH identity"

# Provider selection stays authored and provider-specific fields are passed through
# without forcing every caddy-dns module into Cloudflare's api_token schema.
! grep -A4 '_env_reverse_proxy:' "$repo/ansible/tasks/load-user-vars.yml" \
  | grep -q "'provider': 'cloudflare'" \
  || fail "the Cloudflare secret overlay rewrites the selected DNS provider"
grep -q "rejectattr('key', 'in', \['provider'\] + _caddy_challenge_option_names)" \
  "$repo/ansible/roles/caddy/tasks/main.yml" \
  || fail "Caddy does not preserve provider-specific DNS module options"
grep -q '^      ExecReload=$' "$repo/ansible/roles/caddy/tasks/main.yml" \
  || fail "Caddy reload can reapply the empty-route base config"

python3 "$repo/rundeck/render-job.py" \
  "$repo/rundeck/jobs/vaultwarden-cutover.yaml" > "$work/cutover-job.yml"
grep -q 'storagePath: keys/project/homelab-infra/bootstrap/cloudflare-api-token' \
  "$work/cutover-job.yml" || fail "cutover cannot migrate the Cloudflare token"

if printf '[]' | python3 "$repo/ansible/scripts/vault-runtime.py" \
     --require homelab-infra/proxmox >/dev/null 2>&1; then
  fail "missing required item did not fail"
fi

# Generated facts reject and remove secret-shaped fields without removing IDs.
printf '%s' '{"backups":{"api_token_id":"id","api_token_secret":"secret"}}' \
  | python3 "$repo/ansible/scripts/secret-shape.py" >/dev/null 2>&1 \
  && fail "secret-shaped generated fact passed"
printf '%s\n' 'backups:' '  api_token_id: id' '  api_token_secret: secret' \
  > "$work/facts.yml"
python3 "$repo/ansible/scripts/secret-shape.py" --sanitize "$work/facts.yml"
grep -q 'api_token_id' "$work/facts.yml" || fail "token identifier was removed"
! grep -q 'api_token_secret' "$work/facts.yml" || fail "token secret survived sanitization"
printf '%s\n' 'app:' '  api_key: app-secret' '  port: 8080' > "$work/app.yml"
python3 "$repo/ansible/scripts/secret-shape.py" --extract "$work/app.yml" > "$work/app-secrets.json"
python3 - "$work/app-secrets.json" <<'PY'
import json, sys
assert json.load(open(sys.argv[1])) == {"api_key": "app-secret"}
PY

# Fail closed before invoking Ansible in Seed mode.
set +e
seed_output="$(LAB_REPO="$repo" LAB_VENV="$HOME/.venvs/homelab-ansible" LAB_REFRESH=0 LAB_DOCTOR=0 \
  LAB_ENV_FILE="$work/missing.env" bash "$repo/ansible/scripts/lab-run.sh" \
  playbooks/bootstrap.yml 2>&1)"
seed_rc=$?
set -e
[ "$seed_rc" -ne 0 ] || fail "ordinary bootstrap ran before cutover"
grep -q 'runner is in Seed mode' <<<"$seed_output" || fail "Seed-mode error was not actionable"

# A recreated seed file cannot bypass a Vault marker; LAB_SEED_MODE is rejected.
mkdir -p "$work/state"
touch "$work/state/vault-mode"
set +e
bypass_output="$(LAB_REPO="$repo" LAB_VENV="$HOME/.venvs/homelab-ansible" LAB_REFRESH=0 LAB_DOCTOR=0 \
  LAB_STATE_DIR="$work/state" LAB_SEED_MODE=1 \
  LAB_ENV_FILE="$work/missing.env" bash "$repo/ansible/scripts/lab-run.sh" \
  playbooks/apps/vaultwarden.yml 2>&1)"
bypass_rc=$?
set -e
[ "$bypass_rc" -ne 0 ] || fail "Seed mode bypassed a Vault marker"
grep -q 'already in Vault mode' <<<"$bypass_output" || fail "Vault-mode bypass error was not actionable"

# Runtime preflight redacts values and always locks/logs out/removes CLI state,
# even when the child playbook fails.
mkdir -p "$work/repo" "$work/config" "$work/venv/bin" "$work/fake-bin"
  mkdir -p "$work/repo/ansible"
  ln -s "$repo/ansible/scripts" "$work/repo/ansible/scripts"
  ln -s "$repo/ansible/playbooks" "$work/repo/ansible/playbooks"
ln -s "$work/config" "$work/repo/config"
printf '%s\n' 'proxmox:' '  api_host: 127.0.0.1' '  api_port: 8006' \
  '  api_user: homelab@pve' '  api_token_id: automation' > "$work/config/proxmox.yml"
cat > "$work/venv/bin/ansible-playbook" <<'SH'
#!/bin/sh
exit 9
SH
chmod +x "$work/venv/bin/ansible-playbook"
cat > "$work/fake-bin/bw" <<'SH'
#!/bin/sh
echo "$1 ${BITWARDENCLI_APPDATA_DIR:-}" >> "$FAKE_BW_LOG"
case "$1 $2" in
  'unlock --passwordenv') printf '%s\n' 'session-value' ;;
  'list items') printf '%s' '[
    {"name":"homelab-infra/proxmox","fields":[{"name":"api_token_secret","value":"redaction-probe"}]},
    {"name":"homelab-infra/runner","fields":[{"name":"ssh_private_key","value":"fake-private-key"}]},
    {"name":"homelab-infra/vaultwarden","fields":[{"name":"admin_token","value":"admin-probe"}]}
  ]' ;;
esac
exit 0
SH
chmod +x "$work/fake-bin/bw"
: > "$work/bw.log"

# Confirmed recovery is the only marker-bearing Seed-mode path that deliberately
# skips a vault preflight; the recovery playbook itself owns marker removal.
set +e
recovery_output="$(PATH="$work/fake-bin:$PATH" FAKE_BW_LOG="$work/bw.log" \
  LAB_REPO="$work/repo" LAB_VENV="$work/venv" LAB_REFRESH=0 LAB_DOCTOR=0 \
  LAB_STATE_DIR="$work/state" LAB_SEED_MODE=1 LAB_ENV_FILE="$work/missing.env" \
  PROXMOX_API_TOKEN=recovery-seed \
  bash "$repo/ansible/scripts/lab-run.sh" \
  playbooks/maintenance/vaultwarden-recovery.yml 2>&1)"
recovery_rc=$?
set -e
[ "$recovery_rc" -eq 9 ] || fail "explicit recovery did not reach its guarded playbook"
[ ! -s "$work/bw.log" ] || fail "explicit recovery deadlocked on vault preflight"

set +e
runtime_output="$(PATH="$work/fake-bin:$PATH" FAKE_BW_LOG="$work/bw.log" \
  LAB_REPO="$work/repo" LAB_VENV="$work/venv" LAB_REFRESH=0 LAB_DOCTOR=0 \
  LAB_STATE_DIR="$work/state" LAB_ENV_FILE="$work/missing.env" \
  BW_SERVER=https://vault.example BW_CLIENTID=id BW_CLIENTSECRET=secret BW_PASSWORD=password \
  bash "$repo/ansible/scripts/lab-run.sh" playbooks/bootstrap.yml 2>&1)"
runtime_rc=$?
set -e
[ "$runtime_rc" -eq 9 ] || fail "child failure was not preserved through cleanup"
! grep -q 'redaction-probe\|admin-probe\|session-value' <<<"$runtime_output" \
  || fail "secret appeared in lab-run output"
grep -q '^lock ' "$work/bw.log" || fail "bw lock was not called"
grep -q '^logout ' "$work/bw.log" || fail "bw logout was not called"
appdata="$(awk 'NR==1 {print $2}' "$work/bw.log")"
[ -n "$appdata" ] && [ ! -e "$appdata" ] || fail "private CLI state survived cleanup"

# The collection grant rewrites the collection object wholesale, so the payload it
# sends is exercised here rather than trusted: the shipped shell block is lifted out
# of the task file itself and run against a fake `bw`, so a copy in this test cannot
# drift away from what deploys actually send.
python3 - "$repo/ansible/tasks/bitwarden/upsert-item.yml" > "$work/grant.sh" <<'PY'
import sys, yaml
tasks = yaml.safe_load(open(sys.argv[1]))
name = "Vault | Grant the account explicit access to the canonical collection"
block = [t for t in tasks if t.get("name") == name]
assert len(block) == 1, f"expected exactly one {name!r} task, found {len(block)}"
sys.stdout.write(block[0]["ansible.builtin.shell"])
PY
mkdir -p "$work/grant-bin"
cat > "$work/grant-bin/bw" <<'SH'
#!/bin/sh
case "$1" in
  encode) cat ;;
  edit) cat > "$GRANT_PAYLOAD" ;;
esac
SH
chmod +x "$work/grant-bin/bw"
PATH="$work/grant-bin:$PATH" GRANT_PAYLOAD="$work/grant-payload.json" \
  VAULT_COLLECTION_ID=col VAULT_ORG_ID=org VAULT_SELF_MEMBER_ID=self \
  VAULT_COLLECTION_JSON='{"id":"col","name":"platform-secrets","externalId":null,
    "groups":[],"users":[{"id":"owner","readOnly":false,"hidePasswords":false,"manage":true},
                         {"id":"self","readOnly":true,"hidePasswords":true,"manage":false}]}' \
  bash "$work/grant.sh"
python3 - "$work/grant-payload.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
users = {u["id"]: u for u in payload["users"]}
assert set(users) == {"owner", "self"}, f"grant changed the member set: {sorted(users)}"
assert users["owner"] == {"id": "owner", "readOnly": False,
                          "hidePasswords": False, "manage": True}, "another member's grant was rewritten"
assert users["self"]["manage"] is True, "the account was not granted manage"
assert users["self"]["readOnly"] is False and users["self"]["hidePasswords"] is False, \
    "a stale read-only grant survived"
assert payload["name"] == "platform-secrets" and payload["groups"] == [], \
    "the collection object lost fields the edit must preserve"
PY

echo "Vaultwarden focused tests passed."
