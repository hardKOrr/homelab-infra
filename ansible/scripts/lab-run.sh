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
      LAB_REPO|LAB_VENV|LAB_BRANCH|LAB_REFRESH|LAB_DOCTOR|LAB_SSH_KEY|LAB_OPTION_DIR|BW_SERVER|RUNDECK_URL|RUNDECK_PROJECT)
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
      PROXMOX_API_TOKEN|PROXMOX_API_TOKEN_ID|PROXMOX_API_USER|VAULTWARDEN_ADMIN_TOKEN|LAB_DNS_API_KEY|LAB_DNS_API_SECRET)
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
export BW_SERVER RUNDECK_URL RUNDECK_PROJECT LAB_STATE_DIR LAB_BRANCH
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
  # Exported as well as logged: ansible/callback_plugins/homelab.py prints it in the
  # run header, so the revision sits next to the result rather than scrolled off the
  # top of the pane. A checkout without git simply omits the row.
  LAB_REVISION="$(git -C "$LAB_REPO" log --oneline -1 2>/dev/null || echo unknown)"
  export LAB_REVISION
  log "revision $LAB_REVISION"
fi

# Rundeck's log viewer renders ANSI sequences, but ansible sees a pipe rather than a
# terminal under any job runner and drops colour on its own. Forcing it is what makes
# the callback's red FAILED red in the browser. NO_COLOR still wins, and an operator
# capturing a log to a file can set ANSIBLE_NOCOLOR=1 for one run.
if [ -z "${ANSIBLE_FORCE_COLOR:-}" ] && [ -z "${NO_COLOR:-}" ] && [ -z "${ANSIBLE_NOCOLOR:-}" ]; then
  export ANSIBLE_FORCE_COLOR=1
fi

# EVERY FATAL CHECK BELONGS BELOW THE REFRESH, NEVER ABOVE IT.
#
# The refresh re-execs this script from the tree it just reset, so anything above it
# runs from the copy the runner already had. A check that exits above the refresh can
# never be repaired by pushing a commit — the runner dies before it fetches the fix,
# and the only way out is editing the checkout by hand on the runner. That is exactly
# how the Vault-mode key check below stranded the first job after cutover.
#
# In Vault mode the runner key is deliberately absent from disk: cutover removes it
# once the canonical item verifies, and the Vaultwarden preflight writes it into the
# session temp directory on every run. An unreadable path is therefore the expected
# state after cutover, and only Seed mode can insist on the file. A Vault-mode run
# whose vault has no key still dies in that preflight, naming the missing field.
if [ -n "${LAB_SSH_KEY:-}" ]; then
  if [ -r "$LAB_SSH_KEY" ]; then
    export ANSIBLE_PRIVATE_KEY_FILE="${ANSIBLE_PRIVATE_KEY_FILE:-$LAB_SSH_KEY}"
  elif [ ! -f "$LAB_VAULT_MARKER" ]; then
    die "LAB_SSH_KEY is not readable: $LAB_SSH_KEY"
  fi
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

  # `bw config server` only writes local app data, so it succeeds even when Vaultwarden is
  # down. The failure then surfaced at `bw login` as "API-key authentication", which points
  # the reader at a rotated credential when the real cause is a stopped service — it caused
  # exactly that misdiagnosis on 2026-08-03, during the run that deliberately stopped
  # Vaultwarden to prove this guard works.
  #
  # The probe DIAGNOSES ONLY — it never decides whether to continue. A reachability check
  # that could veto the run would be a second, weaker gate standing in front of the real
  # one, and it would stop a lab whose vault serves the API but not /alive, or that reaches
  # it by a path curl does not share. `bw login` stays the sole arbiter; the probe only
  # chooses which explanation is printed when login fails. It is skipped when curl is
  # absent, so the preflight gains no new runtime dependency.
  _vault_reachable=1
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 10 -o /dev/null "${BW_SERVER%/}/alive" >/dev/null 2>&1 \
      || _vault_reachable=0
  fi

  if ! bw login --apikey >/dev/null 2>&1; then
    [ "$_vault_reachable" -eq 1 ] \
      || die "Vaultwarden at $BW_SERVER did not answer. Vault mode is fail-closed, so this deploy stops here having changed nothing. Start Vaultwarden, or use the explicit recovery workflow."
    die "Vaultwarden preflight failed during API-key authentication — the server answered, so this is a credential problem, not an outage"
  fi
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
  # Run through the interpreter rather than relying on the exec bit. vault-runtime.py is
  # mode 644 in git, so executing it directly fails with "Permission denied" — and that
  # failure then reports itself as a rejected item set, which it is not. Every other
  # script here is likewise invoked as `bash x.sh` / `python3 x.py`.
  HOMELABINFRA_VAULT_JSON="$(printf '%s' "$_vault_items" \
    | python3 "$LAB_REPO/ansible/scripts/vault-runtime.py" "${_vault_require[@]}")" \
    || die "Vaultwarden preflight failed (item set rejected, or vault-runtime.py could not run) — see the error above"
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
# Recovery is exempt, and must be: it is the one path back to Seed mode, and it runs
# precisely when Vaultwarden cannot be reached. It skips the vault preflight by design,
# so no Proxmox token is resolved, so the doctor fails on a missing api_token_secret —
# which made break-glass unreachable exactly when it was needed. Restoring the token from
# a seed file does not help either: seed files are ignored while the marker exists, and
# removing the marker is what recovery is for. The playbook reads no config; it asserts a
# typed confirmation and removes the marker, touching no infrastructure.
if [ "$LAB_DOCTOR" != "0" ] && [ "$_lab_recovery" != "1" ]; then
  PYTHON="${PYTHON:-$LAB_VENV/bin/python3}" \
    bash scripts/config-doctor.sh "$LAB_REPO/config" \
    || die "config-doctor found errors — nothing was changed. Fix them with the Configure App job, or see Get Config."
fi

# ── Instance lists for the job forms ──────────────────────────────────────────
# Every generated per-application job offers its instances as a dropdown sourced from
# these files. Rewriting them before and after Ansible is what keeps the lists current
# without a reimport: a Configure job publishes the file it just created before it exits,
# so the next form already contains it. Best-effort — a lab whose runner has no such
# directory still runs every job, it just types instance names instead of picking them.
LAB_OPTION_DIR="${LAB_OPTION_DIR:-/var/lib/rundeck/app-instances}"
_lab_refresh_instance_lists() {
  [ "$_lab_configured" = "1" ] || return 0
  "$LAB_VENV/bin/python3" "$LAB_REPO/ansible/scripts/app-instances.py" \
    --repo "$LAB_REPO" --out "$LAB_OPTION_DIR" \
    || log "instance lists not refreshed — the job forms fall back to typed names"
}
_lab_refresh_instance_lists

# ── Run ───────────────────────────────────────────────────────────────────────
playbook="$1"; shift
[ -f "$playbook" ] || die "playbook not found: $LAB_REPO/ansible/$playbook"

log "ansible-playbook $playbook $*"
# Recovery runs without the Proxmox wrapper for the same reason it runs without the
# doctor: it reaches no Proxmox API and cannot resolve a token when the vault is down,
# which is when it runs. Every other playbook goes through the wrapper, which resolves
# the connection and fails loudly if it cannot.
_lab_run_status=0
if [ "$_lab_recovery" = "1" ]; then
  "$ANSIBLE_PLAYBOOK" -i inventory/ "$playbook" "$@" || _lab_run_status=$?
else
  bash scripts/with-proxmox-env.sh ../config/proxmox.yml \
    "$ANSIBLE_PLAYBOOK" -i inventory/ "$playbook" "$@" || _lab_run_status=$?
fi

# Configure App writes config/apps/<instance>.yml during the playbook. Refresh again after
# it returns so the new instance is present when the operator opens the next job form; the
# pre-run refresh alone was necessarily one execution behind.
_lab_refresh_instance_lists
exit "$_lab_run_status"
