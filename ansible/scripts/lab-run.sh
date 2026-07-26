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
#   3. runs config-doctor.sh — a missing key fails here, at the front door,
#      instead of as a stack trace mid-provision
#   4. execs ansible-playbook through with-proxmox-env.sh, which exports the
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
#   LAB_ENV_FILE   env file to source              (default: /etc/homelab-infra/lab-run.env)
#   LAB_SECRETS_FILE  operator-supplied secrets    (default: <env dir>/secrets.env)
#   LAB_SECRETS_DIR   platform-written secrets     (default: <env dir>/secrets.d)
#   PROXMOX_API_TOKEN, VAULTWARDEN_ADMIN_TOKEN — from the files above, Key Storage,
#                  or the Semaphore environment; anything already exported wins

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
      LAB_REPO|LAB_VENV|LAB_BRANCH|LAB_REFRESH|LAB_DOCTOR)
        [ -n "${!_key:-}" ] || printf -v "$_key" '%s' "$_value" ;;
    esac
  done < <(grep -E '^LAB_[A-Z_]+=' "$LAB_ENV_FILE" || true)
fi

# The runner's secrets live OUTSIDE the checkout and outside config/, in a file only the
# job user can read (0640 root:rundeck, written by rundeck/bootstrap-rundeck.sh). That is
# what keeps config/proxmox.yml secret-free: the shape is a reviewable, copyable,
# diffable file, and the token is not in it. Rundeck Key Storage holds the same value at
# keys/proxmox/api-token as the durable copy. Anything already exported wins, so a job or
# an operator can override for one run.
#
# secrets.env is written once by the bootstrap script and holds what a human supplied.
# secrets.d/ holds what the PLATFORM generated for itself — today just the Vaultwarden
# admin token, which roles/vaultwarden writes there on first deploy (slice 013). They
# are separate directories because they have different owners and different lifetimes:
# secrets.env is root-owned and never rewritten by a job, secrets.d/ is owned by the
# job user precisely so a job can write into it without privilege escalation.
LAB_SECRETS_FILE="${LAB_SECRETS_FILE:-${LAB_ENV_FILE%/*}/secrets.env}"
LAB_SECRETS_DIR="${LAB_SECRETS_DIR:-${LAB_ENV_FILE%/*}/secrets.d}"

_lab_load_secrets() {
  [ -r "$1" ] || return 0
  while IFS='=' read -r _key _value; do
    case "$_key" in
      PROXMOX_API_TOKEN|PROXMOX_API_TOKEN_ID|PROXMOX_API_USER|VAULTWARDEN_ADMIN_TOKEN)
        [ -n "${!_key:-}" ] || export "$_key=$_value" ;;
    esac
  done < <(grep -E '^[A-Z_]+=' "$1" || true)
}

_lab_load_secrets "$LAB_SECRETS_FILE"
for _secret_file in "$LAB_SECRETS_DIR"/*.env; do
  _lab_load_secrets "$_secret_file"
done

# Fall back to this script's own location: ansible/scripts/lab-run.sh -> repo root.
_self_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
LAB_REPO="${LAB_REPO:-$_self_repo}"
LAB_VENV="${LAB_VENV:-/opt/homelab-ansible}"
LAB_BRANCH="${LAB_BRANCH:-master}"
LAB_DOCTOR="${LAB_DOCTOR:-1}"

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
    export LAB_REFRESHED=1 LAB_REPO LAB_VENV LAB_BRANCH LAB_REFRESH LAB_DOCTOR
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
exec bash scripts/with-proxmox-env.sh ../config/proxmox.yml \
  "$ANSIBLE_PLAYBOOK" -i inventory/ "$playbook" "$@"
