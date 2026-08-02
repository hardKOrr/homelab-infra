#!/usr/bin/env bash
# lab-run.sh — the one entry point every Rundeck and Semaphore job step calls.
#
#   lab-run playbooks/bootstrap.yml
#   lab-run playbooks/apps/remove.yml -e instance=radarr -e delete_data=false
#
# It does, in order:
#   1. resolves the checkout, the ansible venv and the tracked branch from
#      /etc/homelab-infra/lab-run.env (written by rundeck/bootstrap-rundeck.sh),
#      from the environment, or from its own location
#   2. refreshes the checkout to origin/<branch> and echoes the resolved commit,
#      so every job log records the revision that produced the run
#   3. enforces Seed/Vault mode and unlocks Vaultwarden before mutation
#   4. runs config-doctor.sh — a missing key fails here, at the front door,
#      instead of as a stack trace mid-provision
#   5. runs ansible-playbook through with-proxmox-env.sh, which exports the
#      PROXMOX_API_* environment the community.proxmox dynamic inventory reads
#
# Job files therefore carry a playbook name and its arguments and nothing else.
# Path and refresh policy live here, in one file: the lesson of commit 059316a,
# where one wrapper bug failed all 15 jobs identically and one wrapper fix
# repaired them all.
#
# Environment (all optional — the env file supplies them on a bootstrapped runner):
#   LAB_REPO       path to the checkout            (default: this script's repo root)
#   LAB_VENV       ansible venv root               (default: /opt/homelab-ansible)
#   LAB_BRANCH     branch to track                 (default: master)
#   LAB_REFRESH    1 enables the git refresh; DEFAULTS TO 1 ONLY when the env file
#                  exists (i.e. on a bootstrapped runner) and to 0 everywhere else,
#                  because the refresh is a `git reset --hard`
#                  It also refuses outright when the tree has uncommitted tracked changes.
#   LAB_DOCTOR     0 disables the config check     (default: 1)
#   LAB_SSH_KEY    private key used for guests and delegated PVE node commands
#                  (default: unset; bootstrapped runner supplies it)
#   LAB_ENV_FILE   env file to source              (default: /etc/homelab-infra/lab-run.env)
#   LAB_SECRETS_FILE  temporary Seed input          (default: <env dir>/secrets.env)
#   LAB_SECRETS_DIR   temporary generated Seed data (default: <env dir>/secrets.d)
#   LAB_STATE_DIR     durable non-secret state      (default: <env dir>/state)
#   BW_SERVER         public HTTPS Vaultwarden URL
#   BW_CLIENTID, BW_CLIENTSECRET, BW_PASSWORD — secure runner inputs for Vault mode

set -euo pipefail

log() { printf '\033[1;36m[lab-run]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[lab-run] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$#" -ge 1 ] || die "usage: lab-run <playbook> [ansible-playbook args...]"

# ── Resolve configuration ─────────────────────────────────────────────────────
# The env file is written once at bootstrap and is the only place a path lives on
# the host. Values already in the environment win, so a job or an operator can
# override any of them for one run without editing anything.
LAB_ENV_FILE="${LAB_ENV_FILE:-/etc/homelab-infra/lab-run.env}"
_lab_configured=0
if [ -r "$LAB_ENV_FILE" ]; then
  _lab_configured=1
  while IFS='=' read -r _key _value; do
    case "$_key" in
      LAB_REPO|LAB_VENV|LAB_BRANCH|LAB_REFRESH|LAB_DOCTOR|LAB_SSH_KEY|BW_SERVER|RUNDECK_URL|RUNDECK_PROJECT)
        [ -n "${!_key:-}" ] || printf -v "$_key" '%s' "$_value" ;;
    esac
  done < <(grep -E '^(LAB_[A-Z_]+|BW_SERVER|RUNDECK_URL|RUNDECK_PROJECT)=' "$LAB_ENV_FILE" || true)
fi

# These files are a bounded Seed-mode bridge only. After the marker exists they are never
# sourced; Vaultwarden supplies the Proxmox token, admin token, and runner SSH identity.
LAB_SECRETS_FILE="${LAB_SECRETS_FILE:-${LAB_ENV_FILE%/*}/secrets.env}"
LAB_SECRETS_DIR="${LAB_SECRETS_DIR:-${LAB_ENV_FILE%/*}/secrets.d}"
LAB_STATE_DIR="${LAB_STATE_DIR:-${LAB_ENV_FILE%/*}/state}"
LAB_VAULT_MARKER="${LAB_VAULT_MARKER:-$LAB_STATE_DIR/vault-mode}"

_lab_load_secrets() {
  [ -r "$1" ] || return 0
  while IFS='=' read -r _key _value; do
    case "$_key" in
      PROXMOX_API_TOKEN|PROXMOX_API_TOKEN_ID|PROXMOX_API_USER|VAULTWARDEN_ADMIN_TOKEN)
        [ -n "${!_key:-}" ] || export "$_key=$_value" ;;
    esac
  done < <(grep -E '^[A-Z_]+=' "$1" || true)
}

# Seed files are inaccessible to ordinary jobs after cutover, even if somebody
# recreates them. Explicit recovery removes the marker before invoking Seed mode.
if [ ! -f "$LAB_VAULT_MARKER" ] && [ "${LAB_SEED_MODE:-0}" = "1" ]; then
  _lab_load_secrets "$LAB_SECRETS_FILE"
  for _secret_file in "$LAB_SECRETS_DIR"/*.env; do
    _lab_load_secrets "$_secret_file"
  done
fi

# Key-Storage-backed secure options arrive as environment variables in Rundeck
# OSS. Never interpolate them into an inline script or a command argument.
[ -n "${BW_CLIENTID:-}" ] || export BW_CLIENTID="${RD_OPTION_BW_CLIENTID:-}"
[ -n "${BW_CLIENTSECRET:-}" ] || export BW_CLIENTSECRET="${RD_OPTION_BW_CLIENTSECRET:-}"
[ -n "${BW_PASSWORD:-}" ] || export BW_PASSWORD="${RD_OPTION_BW_PASSWORD:-}"
[ -n "${VAULTWARDEN_ADMIN_TOKEN:-}" ] || export VAULTWARDEN_ADMIN_TOKEN="${RD_OPTION_VAULTWARDEN_ADMIN_TOKEN:-}"
[ -n "${RUNDECK_API_TOKEN:-}" ] || export RUNDECK_API_TOKEN="${RD_OPTION_RUNDECK_API_TOKEN:-}"
[ -n "${CLOUDFLARE_API_TOKEN:-}" ] || export CLOUDFLARE_API_TOKEN="${RD_OPTION_CLOUDFLARE_API_TOKEN:-}"

# Fall back to this script's own location: ansible/scripts/lab-run.sh -> repo root.
_self_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
LAB_REPO="${LAB_REPO:-$_self_repo}"
LAB_VENV="${LAB_VENV:-/opt/homelab-ansible}"
LAB_BRANCH="${LAB_BRANCH:-master}"
LAB_DOCTOR="${LAB_DOCTOR:-1}"
BW_SERVER="${BW_SERVER:-}"
export BW_SERVER RUNDECK_URL RUNDECK_PROJECT LAB_STATE_DIR
if [ -n "${LAB_SSH_KEY:-}" ]; then
  [ -r "$LAB_SSH_KEY" ] || die "LAB_SSH_KEY is not readable: $LAB_SSH_KEY"
  export ANSIBLE_PRIVATE_KEY_FILE="${ANSIBLE_PRIVATE_KEY_FILE:-$LAB_SSH_KEY}"
fi

# THE REFRESH DEFAULTS TO OFF, AND ONLY A BOOTSTRAPPED RUNNER TURNS IT ON.
#
# The refresh is a `git reset --hard`, which destroys uncommitted work in whatever
# checkout it is pointed at. On the runner that is exactly right — the checkout is a
# machine-managed copy of origin/<branch> plus gitignored config, and nothing edits it by
# hand. Anywhere else it is a footgun, and defaulting it on made this script destructive
# when merely executed from a developer's working tree, which is where it also lives.
#
# So: refresh only when /etc/homelab-infra/lab-run.env exists (which only
# rundeck/bootstrap-rundeck.sh creates) or when LAB_REFRESH=1 is passed deliberately.
LAB_REFRESH="${LAB_REFRESH:-$([ "$_lab_configured" = "1" ] && echo 1 || echo 0)}"

[ -d "$LAB_REPO/ansible" ] || die "no ansible/ under LAB_REPO=$LAB_REPO"

ANSIBLE_PLAYBOOK="$LAB_VENV/bin/ansible-playbook"
[ -x "$ANSIBLE_PLAYBOOK" ] || {
  ANSIBLE_PLAYBOOK="$(command -v ansible-playbook || true)"
  [ -n "$ANSIBLE_PLAYBOOK" ] || die "ansible-playbook not found in $LAB_VENV/bin or on PATH"
}

# ── Refresh the checkout ──────────────────────────────────────────────────────
# Fire-and-forget means the runner runs current origin/<branch>: a fix pushed to the
# repo reaches the platform on the next click, with no human action. config/ is
# gitignored and untracked, so `reset --hard` cannot touch proxmox.yml,
# infrastructure.yml, .generated/facts.yml, apps/*.yml or .backups/ — the checkout is
# git plus local config, and that invariant is what makes the reset safe.
#
# LAB_REFRESHED guards the re-exec below: this script is itself part of the tree it
# just replaced, so it restarts from the refreshed copy before doing any real work.
if [ "$LAB_REFRESH" = "1" ] && [ -z "${LAB_REFRESHED:-}" ]; then
  if [ -d "$LAB_REPO/.git" ]; then
    # Second, independent safety net: never discard tracked changes someone is holding.
    # A bootstrapped runner never has any — its checkout is only ever written by git — so
    # this costs nothing there and refuses loudly anywhere it would destroy work.
    if [ -n "$(git -C "$LAB_REPO" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
      git -C "$LAB_REPO" status --short --untracked-files=no >&2
      die "$LAB_REPO has uncommitted tracked changes and the refresh would discard them.
       Commit or stash them, or run with LAB_REFRESH=0 to use the tree as it stands."
    fi
    log "refreshing $LAB_REPO to origin/$LAB_BRANCH"
    git config --global --add safe.directory "$LAB_REPO" 2>/dev/null || true
    git -C "$LAB_REPO" fetch --quiet --prune origin "$LAB_BRANCH" \
      || die "git fetch failed — set LAB_REFRESH=0 to run the on-disk checkout unchanged"
    git -C "$LAB_REPO" checkout --quiet -B "$LAB_BRANCH" "origin/$LAB_BRANCH"
    git -C "$LAB_REPO" reset --hard --quiet "origin/$LAB_BRANCH"
    export LAB_REFRESHED=1 LAB_REPO LAB_VENV LAB_BRANCH LAB_REFRESH LAB_DOCTOR BW_SERVER RUNDECK_URL RUNDECK_PROJECT
    export LAB_SSH_KEY="${LAB_SSH_KEY:-}"
    exec bash "$LAB_REPO/ansible/scripts/lab-run.sh" "$@"
  fi
  log "LAB_REPO is not a git checkout — skipping refresh"
elif [ "$LAB_REFRESH" != "1" ]; then
  log "refresh off — running $LAB_REPO as it stands"
fi

if [ -d "$LAB_REPO/.git" ]; then
  log "revision $(git -C "$LAB_REPO" log --oneline -1 2>/dev/null || echo unknown)"
fi

cd "$LAB_REPO/ansible"

# ── Seed/cutover guard and Vaultwarden preflight ──
playbook="$1"
_lab_seed_allowed=0
_lab_recovery=0
case "$playbook" in
  playbooks/apps/vaultwarden.yml|playbooks/apps/caddy.yml|\
  playbooks/maintenance/vaultwarden-enroll.yml|\
  playbooks/maintenance/vaultwarden-cutover.yml|\
  playbooks/maintenance/vaultwarden-recovery.yml)
    _lab_seed_allowed=1 ;;
esac
[ "$playbook" = "playbooks/maintenance/vaultwarden-recovery.yml" ] && _lab_recovery=1

if [ ! -f "$LAB_VAULT_MARKER" ]; then
  if [ "${LAB_SEED_MODE:-0}" != "1" ] || [ "$_lab_seed_allowed" != "1" ]; then
    die "runner is in Seed mode; ordinary jobs are disabled. Complete Vaultwarden enrollment and run Vaultwarden Cutover."
  fi
  log "Seed mode — allowing explicit bootstrap/recovery playbook $playbook"
elif [ "${LAB_SEED_MODE:-0}" = "1" ] && [ "$playbook" != "playbooks/maintenance/vaultwarden-recovery.yml" ]; then
  die "runner is already in Vault mode; LAB_SEED_MODE cannot bypass Vaultwarden"
fi

_vault_tmp=""
_vault_cleanup() {
  if [ -n "$_vault_tmp" ]; then
    bw lock >/dev/null 2>&1 || true
    bw logout >/dev/null 2>&1 || true
    rm -rf -- "$_vault_tmp"
  fi
  unset BW_SESSION HOMELABINFRA_VAULT_JSON
}

_vault_preflight() {
  command -v bw >/dev/null 2>&1 || die "Bitwarden CLI (bw) is not installed"
  [ -n "$BW_SERVER" ] || die "BW_SERVER is not configured"
  [ -n "${BW_CLIENTID:-}" ] || die "BW_CLIENTID was not supplied by secure job storage"
  [ -n "${BW_CLIENTSECRET:-}" ] || die "BW_CLIENTSECRET was not supplied by secure job storage"
  [ -n "${BW_PASSWORD:-}" ] || die "BW_PASSWORD was not supplied by secure job storage"

  _vault_tmp="$(mktemp -d "${TMPDIR:-/tmp}/homelab-bw.XXXXXX")"
  chmod 0700 "$_vault_tmp"
  export BITWARDENCLI_APPDATA_DIR="$_vault_tmp"
  trap _vault_cleanup EXIT
  trap 'exit 130' HUP INT TERM

  bw config server "$BW_SERVER" >/dev/null 2>&1 \
    || die "Vaultwarden preflight failed while configuring the server URL"
  bw login --apikey >/dev/null 2>&1 \
    || die "Vaultwarden preflight failed during API-key authentication"
  BW_SESSION="$(bw unlock --passwordenv BW_PASSWORD --raw 2>/dev/null)" \
    || die "Vaultwarden preflight failed while unlocking the automation vault"
  [ -n "$BW_SESSION" ] || die "Vaultwarden returned an empty vault session"
  export BW_SESSION
  bw sync >/dev/null 2>&1 || die "Vaultwarden preflight failed while synchronizing"

  _vault_items="$(bw list items 2>/dev/null)" \
    || die "Vaultwarden preflight could not read canonical items"
  _vault_require=()
  if [ -f "$LAB_VAULT_MARKER" ]; then
    _vault_require=(
      --require homelab-infra/proxmox
      --require homelab-infra/runner
      --require homelab-infra/vaultwarden
    )
  fi
  HOMELABINFRA_VAULT_JSON="$(printf '%s' "$_vault_items" \
    | "$LAB_REPO/ansible/scripts/vault-runtime.py" "${_vault_require[@]}")" \
    || die "Vaultwarden preflight rejected the canonical item set"
  unset _vault_items
  export HOMELABINFRA_VAULT_JSON

  eval "$(printf '%s' "$HOMELABINFRA_VAULT_JSON" | python3 -c '
import json, shlex, sys
d=json.load(sys.stdin)
p=d.get("proxmox", {})
v=d.get("vaultwarden", {})
for key, value in (("PROXMOX_API_TOKEN", p.get("api_token_secret", "")),
                   ("VAULTWARDEN_ADMIN_TOKEN", v.get("admin_token", ""))):
    if value:
        print("export %s=%s" % (key, shlex.quote(value)))
')"

  _vault_ssh_key="$_vault_tmp/ssh-key"
  if printf '%s' "$HOMELABINFRA_VAULT_JSON" | python3 -c '
import json, sys
value=json.load(sys.stdin).get("runner", {}).get("ssh_private_key", "")
if not value:
    raise SystemExit(3)
sys.stdout.write(value.rstrip("\n") + "\n")
' > "$_vault_ssh_key"; then
    chmod 0600 "$_vault_ssh_key"
    export ANSIBLE_PRIVATE_KEY_FILE="$_vault_ssh_key"
  elif [ -f "$LAB_VAULT_MARKER" ]; then
    die "homelab-infra/runner has no ssh_private_key field"
  fi
  log "Vaultwarden preflight complete"
}

if { [ -f "$LAB_VAULT_MARKER" ] || [ "${LAB_VAULT_PREFLIGHT:-0}" = "1" ]; } \
   && [ "$_lab_recovery" != "1" ]; then
  _vault_preflight
fi

# ── Validate config before anything mutates ───────────────────────────────────
if [ "$LAB_DOCTOR" != "0" ]; then
  PYTHON="${PYTHON:-$LAB_VENV/bin/python3}" \
    bash scripts/config-doctor.sh "$LAB_REPO/config" \
    || die "config-doctor found errors — nothing was changed. Fix them with the Configure App job, or see Get Config."
fi

# ── Run ───────────────────────────────────────────────────────────────────────
playbook="$1"; shift
[ -f "$playbook" ] || die "playbook not found: $LAB_REPO/ansible/$playbook"

log "ansible-playbook $playbook $*"
bash scripts/with-proxmox-env.sh ../config/proxmox.yml \
  "$ANSIBLE_PLAYBOOK" -i inventory/ "$playbook" "$@"
