#!/usr/bin/env bash
# bootstrap-rundeck.sh — stand up the whole homelab-infra runner from nothing.
#
# Run this ON a Proxmox node, as root. By default it returns a working automation
# runner, Caddy, and HTTPS Vaultwarden. Open Rundeck, complete the human enrollment
# ceremony and verified cutover, then click Bootstrap Platform for the baseline.
#
#   ./bootstrap-rundeck.sh
#   VMID=13228 CT_IP=192.168.13.228/20 CT_GW=192.168.13.1 ./bootstrap-rundeck.sh  # skip the prompts
#   LAB_DOMAIN=lab.example.com NONINTERACTIVE=1 ./bootstrap-rundeck.sh
#   DEPLOY_VAULTWARDEN=0 ./bootstrap-rundeck.sh  # runner-only recovery
#
# This is the first of the platform's two bootstrap layers:
#
#   1. THIS SCRIPT, on a PVE node — builds the runner and everything the UI needs to
#      exist: the LXC, Java, Rundeck, an ansible venv, the repo clone, the platform's
#      Proxmox credential, the authored config, Key Storage, the project and the jobs.
#      It invokes normal playbooks to deploy Caddy and then Vaultwarden in Seed mode.
#   2. ENROLLMENT/CUTOVER, in the UI — the owner chooses passwords in Vaultwarden,
#      stages the automation API credentials, and verifies every imported seed secret.
#   3. BOOTSTRAP PLATFORM, in the UI — reconciles Caddy/Vaultwarden, then builds Ntfy, Authentik, Uptime Kuma,
#      Prometheus + Grafana and PBS.
#
# One command produces the enrollment surface; the explicit cutover establishes Vault mode.
#
# What it produces:
#   - Unprivileged Debian 13 LXC, tagged _+lab so the platform manages it like
#     any other guest it created — PBS backs it up, Lab Status reports it
#   - OpenJDK 21 + Rundeck 6.x, a random admin password, a non-expiring API token
#   - ansible-core 2.18 in a venv, with the collections pinned in ansible/requirements.yml
#   - A clone of this repo at /var/lib/rundeck/homelab-infra
#   - A dedicated homelab-infra@pve Proxmox user with a scoped role, and its API token —
#     captured in-process and written to Key Storage, never to a config file
#   - A dedicated ed25519 keypair the platform connects to its guests with
#   - config/proxmox.yml, config/infrastructure.yml and config/apps/rundeck.yml, authored
#     from what this node can discover plus a handful of prompts
#   - The Rundeck project, every job in rundeck/jobs/, and staged Key Storage
#
# Idempotent throughout: re-running converges an existing container, never rotates a
# password or token you already have, and never overwrites an answered prompt.
#
# Credentials are written inside the container to /root/.rundeck-bootstrap (0600) and
# summarised at the end. Re-runs read that file back rather than issuing new secrets.

set -euo pipefail

# ── Tunables ───────────────────────────────────────────────────────────────────
# The runner's own identity is deliberately EMPTY here. It is asked in Preflight, with
# defaults read off this node, because it is the one address in the lab the platform
# cannot allocate for itself: every other guest gets an IP from allocate-ip.py, which does
# not exist until this container does. A constant here silently claims one lab's address
# in every lab, and the operator only finds out once the container is built. Setting any
# of these in the environment still skips its prompt, so the scripted path is unchanged.
VMID="${VMID:-}"
CT_HOSTNAME="${CT_HOSTNAME:-}"
CT_IP="${CT_IP:-}"
CT_GW="${CT_GW:-}"
CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_CORES="${CT_CORES:-4}"
CT_MEMORY="${CT_MEMORY:-8192}"
CT_SWAP="${CT_SWAP:-512}"
CT_DISK="${CT_DISK:-16}"
# Left empty on purpose: resolved below from what this node can actually hold a container
# rootfs on. Hardcoding a pool name here would name one lab's storage in everyone's script.
CT_STORAGE="${CT_STORAGE:-}"

TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
TEMPLATE="${TEMPLATE:-debian-13-standard_13.6-1_amd64.tar.zst}"

REPO_URL="${REPO_URL:-https://github.com/hardKOrr/homelab-infra}"
REPO_BRANCH="${REPO_BRANCH:-master}"

# The tags this container carries, in the platform's tag grammar (ansible/inventory/
# proxmox.yml has the whole contract):
#
#   _+lab      the exact ownership sentinel. configure-pbs.yml builds its vzdump vmid list
#              by filtering on it, and status.yml, guest-maintenance.yml and
#              check-native-updates.yml all reach the runner through the `lab_managed`
#              inventory group derived from it — so tagging this container is what puts the
#              host holding the platform's own config inside the platform's own backup and
#              reporting.
#   _-debian   the declared machine fact; the container is built from a Debian template.
#   _rundeck   the application tag: this guest hosts the Rundeck instance.
#
MANAGED_TAGS="${MANAGED_TAGS:-_+lab;_-debian;_rundeck}"

# Proxmox credential. The platform gets its own user and its own scoped role rather than
# borrowing root@pam: a real privilege reduction, and bootstrap is the only moment it is
# cheap to make. privsep 0 means the token carries the user's privileges rather than a
# further-restricted subset — the scoping lives in the role, where it is inspectable.
PVE_USER="${PVE_USER:-homelab-infra@pve}"
PVE_ROLE="${PVE_ROLE:-HomelabInfra}"
PVE_TOKEN_NAME="${PVE_TOKEN_NAME:-automation}"
# A Proxmox token secret can be captured only at creation. It is never printed; re-runs
# keep the existing token unless deliberate rotation is requested.
ROTATE_PROXMOX_TOKEN="${ROTATE_PROXMOX_TOKEN:-0}"

# Deploy Caddy and the preliminary Vaultwarden LXC before this script returns. The script
# deliberately invokes Ansible rather than duplicating app provisioning in Bash:
# later inventory refreshes find the `vaultwarden` tag and the same playbook adopts
# and reconciles the guest. Set to 0 only for runner-only recovery/diagnostics.
DEPLOY_VAULTWARDEN="${DEPLOY_VAULTWARDEN:-1}"

# Root SSH access into the container. Default: whatever keys already authorise root on THIS
# Proxmox node — anyone who can run this script is already node root, so copying them grants no
# new privilege and guarantees the finished container is reachable. Point SSH_PUBKEY_FILE
# somewhere else, or set SSH_PUBKEY to a single literal key, to override.
SSH_PUBKEY_FILE="${SSH_PUBKEY_FILE:-/root/.ssh/authorized_keys}"
SSH_PUBKEY="${SSH_PUBKEY:-}"

ANSIBLE_CORE_SPEC="${ANSIBLE_CORE_SPEC:-ansible-core==2.18.*}"

REPO_DIR=/var/lib/rundeck/homelab-infra
VENV_DIR=/opt/homelab-ansible
CRED_FILE=/root/.rundeck-bootstrap
LAB_ETC=/etc/homelab-infra
# Temporary Seed input. It lives in the job-user-owned secrets.d/ so that cutover can
# delete it without sudo. Runners bootstrapped before that move keep the seed one level
# up, in a root-owned directory; that legacy path is still read, and cutover reports it
# when the job user cannot remove it.
LAB_SEED_ENV="$LAB_ETC/secrets.d/proxmox.env"
LAB_LEGACY_SEED_ENV="$LAB_ETC/secrets.env"
# The platform's own SSH identity, generated in the container. Its public half goes into
# config/proxmox.yml (deployed to every guest this platform creates). Cutover moves the
# private half into Vaultwarden; lab-run materializes it only for one job session.
LAB_SSH_KEY=/var/lib/rundeck/.ssh/homelab-infra

RD_PORT="${RD_PORT:-4440}"
# Derived in Preflight, once CT_IP has been resolved from the environment or the prompt.
RD_HOST=""
RD_URL=""
RD_PROJECT="${RD_PROJECT:-homelab-infra}"
RD_API="${RD_API:-58}"

# Set NONINTERACTIVE=1 to take every default and fail rather than prompt for anything
# that has none. This is the scripted path; the interactive path stays humane.
NONINTERACTIVE="${NONINTERACTIVE:-0}"

# ── Helpers ────────────────────────────────────────────────────────────────────
log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    \033[1;33mWARNING: %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

in_ct() { pct exec "$VMID" -- "$@"; }

# One temp root for everything this script stages, removed however we exit. Secrets pass
# through here on their way into the container and into Key Storage, so it is 0700 and it
# is cleaned on interrupt as well as on normal exit — a single trap, not one per stage.
TMPROOT="$(mktemp -d)"
chmod 0700 "$TMPROOT"
trap 'rm -rf "$TMPROOT"' EXIT INT TERM
newtmp() { mktemp -d "$TMPROOT/stage.XXXXXX"; }

# ct_file_exists <path> — true when the path exists inside the container.
ct_file_exists() { in_ct test -e "$1" >/dev/null 2>&1; }

# push_file <local> <container-path> <mode> — copy a file in, creating parent dirs.
push_file() {
  in_ct mkdir -p "$(dirname "$2")"
  pct push "$VMID" "$1" "$2" --perms "$3"
}

# ask <var-name> <prompt> [default] — resolve a value from the environment, then a
# prompt, then a default. Already-set environment variables are never re-asked, which is
# what keeps the scripted path non-interactive and re-runs free of re-answering.
ask() {
  local __var="$1" __prompt="$2" __default="${3:-}" __reply=""
  if [ -n "${!__var:-}" ]; then
    info "$__var = ${!__var} (from the environment)"
    return 0
  fi
  if [ "$NONINTERACTIVE" = "1" ] || [ ! -t 0 ]; then
    [ -n "$__default" ] || die "$__var has no default and cannot be prompted for (NONINTERACTIVE=1). Set $__var=..."
    printf -v "$__var" '%s' "$__default"
    info "$__var = $__default (default)"
    return 0
  fi
  if [ -n "$__default" ]; then
    read -r -p "    $__prompt [$__default]: " __reply || true
    __reply="${__reply:-$__default}"
  else
    while [ -z "$__reply" ]; do
      read -r -p "    $__prompt: " __reply || true
    done
  fi
  printf -v "$__var" '%s' "$__reply"
}

# net_addr <a.b.c.d/nn> — the NETWORK address of that CIDR. Masking only the last octet
# is wrong for every prefix shorter than /24: 192.168.13.228/20 is not 192.168.13.0/20,
# it is 192.168.0.0/20, and the difference is what config/infrastructure.yml records as
# the lab's guest network.
net_addr() {
  local __ip="${1%%/*}" __pfx="${1##*/}" __o1 __o2 __o3 __o4 __a __m __n
  IFS=. read -r __o1 __o2 __o3 __o4 <<<"$__ip"
  __a=$(( (__o1 << 24) + (__o2 << 16) + (__o3 << 8) + __o4 ))
  if [ "$__pfx" -eq 0 ] 2>/dev/null; then __m=0; else __m=$(( (0xFFFFFFFF << (32 - __pfx)) & 0xFFFFFFFF )); fi
  __n=$(( __a & __m ))
  printf '%d.%d.%d.%d/%d\n' \
    $(( (__n >> 24) & 255 )) $(( (__n >> 16) & 255 )) \
    $(( (__n >> 8) & 255 )) $(( __n & 255 )) "$__pfx"
}

# ask_secret <var-name> <prompt> — like ask, but never echoes either an environment
# value or an interactive reply. Secrets have no useful default: a non-interactive
# first bootstrap must supply them explicitly in the environment.
ask_secret() {
  local __var="$1" __prompt="$2" __reply=""
  if [ -n "${!__var:-}" ]; then
    info "$__var supplied by the environment"
    return 0
  fi
  if [ "$NONINTERACTIVE" = "1" ] || [ ! -t 0 ]; then
    die "$__var is required and cannot be prompted for. Supply it in the environment."
  fi
  while [ -z "$__reply" ]; do
    read -r -s -p "    $__prompt: " __reply || true
    printf '\n'
  done
  printf -v "$__var" '%s' "$__reply"
}

# ── Preflight ──────────────────────────────────────────────────────────────────
log "Preflight"
[ "$(id -u)" -eq 0 ] || die "must run as root on a Proxmox node"
command -v pct >/dev/null    || die "pct not found — run this on a Proxmox node, not inside a container"
command -v pveam >/dev/null  || die "pveam not found"
command -v pveum >/dev/null  || die "pveum not found"
case "$DEPLOY_VAULTWARDEN" in
  0|1) : ;;
  *) die "DEPLOY_VAULTWARDEN must be 0 or 1 (got '$DEPLOY_VAULTWARDEN')" ;;
esac

# The runner container's own identity is ASKED, never assumed. Converging an EXISTING
# runner defaults to what that container already is, so a re-run never proposes moving
# the control plane; a fresh one defaults to this node's own subnet and next free VMID.
# Resolve the storage that will hold every guest this platform creates. `rootdir` is the
# content type a container rootfs needs, and a stock Proxmox `local` does NOT carry it — it
# is vztmpl/iso/backup, which is why a default of "local" fails every create with
# "storage 'local' does not support container directories". Ask the node instead of guessing.
CT_STORAGE_DEFAULT="$(pvesm status --content rootdir 2>/dev/null | awk 'NR>1 && $3 == "active" {print $1; exit}')"
NODE_ADDR_CIDR="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4; exit}')"
NODE_PREFIX="${NODE_ADDR_CIDR##*/}"
NODE_SUBNET="${NODE_ADDR_CIDR%.*}"
NODE_GW_DEFAULT="$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')"
VMID_DEFAULT="$(pvesh get /cluster/nextid 2>/dev/null | tr -d '"[:space:]')"

info "the runner's own identity - defaults are read off this node"
ask VMID "Runner VMID" "$VMID_DEFAULT"

CT_HOSTNAME_DEFAULT="homelab-rundeck"
CT_IP_DEFAULT=""
if [ -n "$NODE_SUBNET" ] && [ -n "$NODE_PREFIX" ]; then
  CT_IP_DEFAULT="${NODE_SUBNET}.228/${NODE_PREFIX}"
fi
CT_GW_DEFAULT="$NODE_GW_DEFAULT"
if pct config "$VMID" >/dev/null 2>&1; then
  info "VMID $VMID already exists - its current settings become the defaults"
  CT_CONF="$(pct config "$VMID")"
  CT_HOSTNAME_DEFAULT="$(sed -n 's/^hostname: //p'            <<<"$CT_CONF")"
  CT_IP_DEFAULT="$(sed -n 's/^net0: .*,ip=\([^,]*\).*/\1/p'   <<<"$CT_CONF")"
  CT_GW_DEFAULT="$(sed -n 's/^net0: .*,gw=\([^,]*\).*/\1/p'   <<<"$CT_CONF")"
  CT_STORAGE_DEFAULT="$(sed -n 's/^rootfs: \([^:]*\):.*/\1/p' <<<"$CT_CONF")"
fi

ask CT_HOSTNAME "Runner hostname"                            "$CT_HOSTNAME_DEFAULT"
ask CT_IP       "Runner address, with prefix (a.b.c.d/nn)"   "$CT_IP_DEFAULT"
ask CT_GW       "Runner gateway"                             "$CT_GW_DEFAULT"
ask CT_STORAGE  "Storage for the runner rootfs"              "$CT_STORAGE_DEFAULT"

case "$CT_IP" in
  */*) : ;;
  *) die "CT_IP must carry a prefix length, as in 192.168.13.228/20 (got '$CT_IP')" ;;
esac
[ -n "$CT_GW" ] || die "CT_GW has no value and this node advertises no default route. Set CT_GW=..."
[ -n "$CT_STORAGE" ] || die "no active storage on this node supports container rootfs (content type 'rootdir').
Add one in Proxmox, or set CT_STORAGE=... if you know better."

RD_HOST="${CT_IP%%/*}"
RD_URL="http://${RD_HOST}:${RD_PORT}"

pvesm status --storage "$CT_STORAGE" >/dev/null 2>&1 || die "storage '$CT_STORAGE' not available on this node"
pvesm status --content rootdir 2>/dev/null | awk 'NR>1 {print $1}' | grep -qxF "$CT_STORAGE" \
  || warn "storage '$CT_STORAGE' does not advertise content type 'rootdir'; container creation may fail"
info "node $(hostname), target VMID $VMID at $RD_URL on $CT_STORAGE"

# Resolve the keys now so we fail before building anything, not after.
if [ -z "$SSH_PUBKEY" ] && [ -r "$SSH_PUBKEY_FILE" ]; then
  SSH_PUBKEY="$(grep -E '^(ssh-|ecdsa-)' "$SSH_PUBKEY_FILE" 2>/dev/null || true)"
fi
if [ -z "$SSH_PUBKEY" ]; then
  warn "no SSH public key found (looked in $SSH_PUBKEY_FILE)"
  info "the container will only be reachable via 'pct exec $VMID -- ...'"
  info "set SSH_PUBKEY=... or SSH_PUBKEY_FILE=... to get root SSH"
else
  info "$(printf '%s\n' "$SSH_PUBKEY" | grep -c .) SSH key(s) will authorise root"
fi

CT_EXISTS=0
if pct status "$VMID" >/dev/null 2>&1; then
  CT_EXISTS=1
  info "container $VMID already exists — converging it"
else
  # Only guard the address when we are about to claim it.
  if ping -c 2 -W 1 "$RD_HOST" >/dev/null 2>&1; then
    die "$RD_HOST already answers ping but VMID $VMID does not exist — pick a free IP/VMID"
  fi
  info "container $VMID does not exist - creating it"
fi

# A Proxmox token secret is readable exactly once, at creation. If the token already
# exists and no runner exists to be holding its Seed copy, nothing downstream can ever
# obtain it, and the Seed phase at the very end of this script will die. Say so HERE,
# before an hour of container, Rundeck and job import work is done for nothing.
if [ "$CT_EXISTS" -eq 0 ] && [ "$ROTATE_PROXMOX_TOKEN" != "1" ] \
   && pveum user token list "$PVE_USER" --output-format json 2>/dev/null \
        | grep -q "\"tokenid\":\"${PVE_TOKEN_NAME}\""; then
  die "the Proxmox token ${PVE_USER}!${PVE_TOKEN_NAME} already exists, but VMID $VMID does not.
A token secret cannot be re-read, and there is no runner holding a copy, so this bootstrap
could not store one. This is the normal state after a teardown: the guests went, the
cluster-level user and token stayed. Re-run with ROTATE_PROXMOX_TOKEN=1 to replace it."
fi

# ── Discover what this node already knows ──────────────────────────────────────
# This script runs as root on the node, so most of config/proxmox.yml is discoverable
# rather than promptable. Nothing below asks a human for a fact a command can answer.
log "Discover node facts"
PVE_NODE="$(hostname)"
# The address the API is reachable on: whichever local address routes toward the
# container's gateway. Falls back to the node's hostname.
PVE_API_HOST="$(ip -4 route get "$CT_GW" 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
PVE_API_HOST="${PVE_API_HOST:-$PVE_NODE}"
NODE_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"
info "node name       $PVE_NODE"
info "API host        $PVE_API_HOST"
info "timezone        $NODE_TZ"
info "storages        $(pvesm status 2>/dev/null | awk 'NR>1 {printf "%s ", $1}')"
info "bridges         $(ip -o link 2>/dev/null | awk -F': ' '$2 ~ /^vmbr/ {printf "%s ", $2}')"
info "vztmpl storage  $(pvesm status --content vztmpl 2>/dev/null | awk 'NR>1 {printf "%s ", $1}')"

# ── Template ───────────────────────────────────────────────────────────────────
if [ "$CT_EXISTS" -eq 0 ]; then
  log "Template"
  if pvesm list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "vztmpl/${TEMPLATE}"; then
    info "$TEMPLATE already present"
  else
    info "downloading $TEMPLATE"
    pveam update >/dev/null 2>&1 || true
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
  fi

  # ── Container ────────────────────────────────────────────────────────────────
  log "Create container $VMID"
  pct create "$VMID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    --hostname "$CT_HOSTNAME" \
    --cores "$CT_CORES" --memory "$CT_MEMORY" --swap "$CT_SWAP" \
    --rootfs "${CT_STORAGE}:${CT_DISK}" \
    --net0 "name=eth0,bridge=${CT_BRIDGE},firewall=1,gw=${CT_GW},ip=${CT_IP},type=veth" \
    --tags "$MANAGED_TAGS" \
    --unprivileged 1 --features nesting=1 --onboot 1 --ostype debian \
    --description "Rundeck — homelab-infra UI layer (bootstrap-rundeck.sh)"
fi

# ── Adopt the runner into the model it runs ────────────────────────────────────
# Applied on every run, not only at creation, so an existing runner built before this
# change joins the model on the next re-run.
log "Adopt the runner as a managed guest"
# Read-modify-write over the whole managed set, and by EXACT membership of each tag: an
# operator tag on this container survives, and a tag that is already there is not written
# twice. Nothing here matches on a `_` prefix — that would claim every platform lane at once.
CURRENT_TAGS="$(pct config "$VMID" | sed -n 's/^tags: //p' || true)"
NEW_TAGS="$CURRENT_TAGS"
ADDED=""
for want in ${MANAGED_TAGS//;/ }; do
  case ";${NEW_TAGS};" in
    *";${want};"*) ;;
    *) NEW_TAGS="${NEW_TAGS:+${NEW_TAGS};}${want}"; ADDED="${ADDED:+${ADDED} }${want}" ;;
  esac
done
if [ -z "$ADDED" ]; then
  info "already tagged $MANAGED_TAGS"
else
  pct set "$VMID" --tags "$NEW_TAGS"
  info "tagged $ADDED (tags now: $NEW_TAGS)"
  info "PBS will now back this container up, and Lab Status will report it"
fi

if [ "$(pct status "$VMID" | awk '{print $2}')" != "running" ]; then
  log "Start container $VMID"
  pct start "$VMID"
fi

log "Wait for container network"
for _ in $(seq 1 30); do
  if in_ct ping -c 1 -W 2 deb.debian.org >/dev/null 2>&1; then break; fi
  sleep 2
done
in_ct ping -c 1 -W 2 deb.debian.org >/dev/null 2>&1 || die "container $VMID has no outbound network"
info "network up"

# ── Guest provisioning ─────────────────────────────────────────────────────────
# Passed through `env` so the heredoc can stay quoted (no host-side expansion surprises).
log "Provision guest (packages, Java, Rundeck, Ansible)"
in_ct env \
  RD_URL="$RD_URL" \
  RD_HOST="$RD_HOST" \
  RD_PROJECT="$RD_PROJECT" \
  CT_HOSTNAME="$CT_HOSTNAME" \
  REPO_URL="$REPO_URL" \
  REPO_BRANCH="$REPO_BRANCH" \
  REPO_DIR="$REPO_DIR" \
  VENV_DIR="$VENV_DIR" \
  CRED_FILE="$CRED_FILE" \
  LAB_ETC="$LAB_ETC" \
  LAB_SSH_KEY="$LAB_SSH_KEY" \
  ANSIBLE_CORE_SPEC="$ANSIBLE_CORE_SPEC" \
  SSH_PUBKEY="$SSH_PUBKEY" \
  bash -s <<'GUEST'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LANG=C.UTF-8 LC_ALL=C.UTF-8
# `pct exec` hands the guest a PATH of /sbin:/bin:/usr/sbin:/usr/bin — no /usr/local/bin.
# Anything this script installs there (npm -g puts the Bitwarden CLI at /usr/local/bin/bw)
# is otherwise invisible to the very steps that install it. Rundeck's own jobs are
# unaffected: systemd's default PATH does include it.
export PATH=/usr/local/sbin:/usr/local/bin:$PATH

say() { printf '    %s\n' "$*"; }

# Replace one handover value without disturbing the others. The values remain shell
# builtins' arguments only; do not hand them to sed/awk through process argv.
cred_set() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if [ -f "$CRED_FILE" ]; then
    grep -v "^${key}=" "$CRED_FILE" > "$tmp" || true
  fi
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  install -m 0600 -o root -g root "$tmp" "$CRED_FILE"
  rm -f "$tmp"
}

# -- locale ---------------------------------------------------------------------
# The minimal Debian LXC image ships no generated locale; ansible refuses to start without
# a UTF-8 one ("could not initialize the preferred locale").
if ! locale -a 2>/dev/null | grep -qi '^en_US.utf8$'; then
  say "generating en_US.UTF-8"
  apt-get update -qq
  apt-get install -y -qq locales >/dev/null
  sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
  locale-gen >/dev/null
  printf 'LANG=en_US.UTF-8\nLC_ALL=en_US.UTF-8\n' > /etc/default/locale
fi

# -- base packages --------------------------------------------------------------
say "base packages"
apt-get update -qq
apt-get -y -qq upgrade
# prometheus-node-exporter is here for the same reason tasks/guest-bootstrap.yml installs
# it on every guest the platform creates: the runner tags ITSELF _+lab, so
# observability's scrape list — built from that tag — includes it. This container is the
# one managed guest guest-bootstrap.yml never touches, because bootstrap-rundeck.sh builds
# it before Ansible exists to run against it. Without the package the runner is a
# permanently DOWN target in Prometheus, which is exactly the host whose health matters
# most: every job runs on it.
apt-get install -y -qq \
  ca-certificates curl gnupg git jq rsync sudo \
  nodejs npm \
  openssh-client openssh-server python3-venv python3-pip \
  unattended-upgrades apt-transport-https prometheus-node-exporter >/dev/null

systemctl enable --now prometheus-node-exporter >/dev/null 2>&1 || true

# -- root SSH ------------------------------------------------------------------
# The Debian LXC template ships root with a locked password and no authorized_keys, so a
# container built purely over `pct exec` is unreachable over SSH. Install keys rather than
# setting a password: sshd's default is PermitRootLogin prohibit-password, which accepts a
# key and rejects a password, so no config change is needed.
if [ -n "${SSH_PUBKEY:-}" ]; then
  say "authorising root SSH keys"
  install -d -m 0700 -o root -g root /root/.ssh
  touch /root/.ssh/authorized_keys
  while IFS= read -r key; do
    [ -n "$key" ] || continue
    grep -qxF "$key" /root/.ssh/authorized_keys || echo "$key" >> /root/.ssh/authorized_keys
  done <<KEYS
$SSH_PUBKEY
KEYS
  chmod 0600 /root/.ssh/authorized_keys
  chown -R root:root /root/.ssh
  say "$(grep -c . /root/.ssh/authorized_keys) key(s) authorised"
  systemctl enable ssh >/dev/null 2>&1 || true
  systemctl restart ssh
else
  say "no SSH key supplied — root SSH will not work; use 'pct exec'"
fi

# -- java 21 (Rundeck 6 requires 17+) -------------------------------------------
say "openjdk 21"
apt-get install -y -qq openjdk-21-jre-headless >/dev/null

# -- rundeck repo + package -----------------------------------------------------
if [ ! -f /etc/apt/keyrings/rundeck.gpg ]; then
  say "adding Rundeck apt repo"
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://packages.rundeck.com/pagerduty/rundeck/gpgkey \
    | gpg --dearmor -o /etc/apt/keyrings/rundeck.gpg
  chmod 0644 /etc/apt/keyrings/rundeck.gpg
  echo "deb [signed-by=/etc/apt/keyrings/rundeck.gpg] https://packages.rundeck.com/pagerduty/rundeck/any/ any main" \
    > /etc/apt/sources.list.d/rundeck.list
  apt-get update -qq
fi

say "rundeck package"
apt-get install -y -qq rundeck >/dev/null
RUNDECK_PACKAGE_VERSION="$(dpkg-query -W -f='${Version}' rundeck)"
dpkg --compare-versions "$RUNDECK_PACKAGE_VERSION" ge 6.0 \
  || { echo "Rundeck 6.0+ is required for AES-GCM Key Storage" >&2; exit 1; }
say "rundeck $RUNDECK_PACKAGE_VERSION (AES-GCM capable)"

# Official Bitwarden CLI. Vault mode cannot start without it, so install it as
# runner infrastructure rather than lazily during the first deploy.
say "Bitwarden CLI"
npm install -g --silent @bitwarden/cli >/dev/null
# Verify explicitly. `say "bw $(bw --version)"` would swallow a missing binary: command
# substitution failing inside an argument leaves `say` itself returning 0, so set -e never
# fires and the run continues to fail much later, at cutover, for no visible reason.
command -v bw >/dev/null || { echo "bitwarden CLI not on PATH after install" >&2; exit 1; }
say "bw $(bw --version 2>/dev/null)"

# -- rundeck config -------------------------------------------------------------
say "configuring $RD_URL"
sed -i "s|^grails.serverURL=.*|grails.serverURL=${RD_URL}|" /etc/rundeck/rundeck-config.properties
sed -i \
  -e "s|^framework.server.name *=.*|framework.server.name = ${CT_HOSTNAME}|" \
  -e "s|^framework.server.hostname *=.*|framework.server.hostname = ${RD_HOST}|" \
  -e "s|^framework.server.url *=.*|framework.server.url = ${RD_URL}|" \
  /etc/rundeck/framework.properties

# Permit non-expiring API tokens; the stock cap is 30 days, which would silently break
# automation a month after bootstrap.
grep -q '^rundeck.api.tokens.duration.max' /etc/rundeck/rundeck-config.properties \
  || echo 'rundeck.api.tokens.duration.max=0' >> /etc/rundeck/rundeck-config.properties

# Permit API calls authenticated by a login session rather than an API token. This is off by
# default, and without it there is no way to bootstrap the FIRST token: issuing one is itself
# an API call, so a fresh Rundeck with no token cannot be given one over the API. The symptom
# is a login that plainly works (the UI returns 200) while every /api/ call answers
# `"(unauthenticated) is not authorized"`, which then skips project creation, Key Storage and
# the whole job import.
grep -q '^rundeck.security.apiCookieAccess.enabled' /etc/rundeck/rundeck-config.properties \
  || echo 'rundeck.security.apiCookieAccess.enabled=true' >> /etc/rundeck/rundeck-config.properties

# Rundeck 6.0+ ships the AES-256-GCM converter. Encrypt the complete Key Storage
# tree. The converter password is deliberately outside Key Storage to avoid a
# circular dependency and is included in the root-only handover file so the
# operator can back it up separately.
#
# The Rundeck 6.1 package already declares this converter with an inline, package-
# generated `config.password`. Merely testing whether `converter.1.type` exists leaves
# that password active and makes the documented password file an unused decoy. A rebuild
# then restores the wrong key and every Key Storage read fails with AEADBadTagException.
# Adopt that existing password before switching the plugin to an environment variable
# supplied by a root-owned systemd EnvironmentFile. The installed 6.1 plugin supports
# password, passwordEnvVarName and passwordSysPropName; it does not support passwordFile.
# Existing environment-backed installs retain their file value; fresh installs generate
# one here.
STORAGE_PASSWORD_FILE=/etc/rundeck/.storage-password
legacy_storage_password="$(sed -n 's/^rundeck\.storage\.converter\.1\.config\.password=//p' \
  /etc/rundeck/rundeck-config.properties | tail -1)"
legacy_config_storage_password="$(sed -n 's/^rundeck\.config\.storage\.converter\.1\.config\.password=//p' \
  /etc/rundeck/rundeck-config.properties | tail -1)"
if grep -q '^rundeck\.storage\.converter\.1\.config\.passwordEnvVarName=' \
     /etc/rundeck/rundeck-config.properties; then
  storage_password="$(sed -n 's/^RUNDECK_STORAGE_PASSWORD=//p' "$STORAGE_PASSWORD_FILE" | tail -1)"
  [ -n "$storage_password" ] \
    || { echo "configured Key Storage environment file is missing or empty" >&2; exit 1; }
elif [ -n "$legacy_storage_password" ]; then
  storage_password="$legacy_storage_password"
elif grep -q '^rundeck\.storage\.converter\.1\.config\.passwordFile=' \
       /etc/rundeck/rundeck-config.properties && [ -s "$STORAGE_PASSWORD_FILE" ]; then
  # Recovery for the short-lived passwordFile declaration shipped before the installed
  # plugin's accepted keys were exercised live. The plugin ignored that file, so the
  # project-config inline value remained the key that had actually encrypted both stores.
  storage_password="${legacy_config_storage_password:-$(cat "$STORAGE_PASSWORD_FILE")}"
else
  storage_pool="$(head -c 128 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
  storage_password="${storage_pool:0:48}"
  unset storage_pool
fi

# Rundeck applies a second instance of the same converter to project configuration.
# Secure job-option definitions are read through this namespace before a job starts. The
# package uses the same password for both; preserving only Key Storage lets the API list a
# restored job but fails its execution with AEADBadTagException. Refuse an unexpected split
# instead of silently making either restored namespace unreadable.
if [ -n "$legacy_config_storage_password" ] \
   && [ "$legacy_config_storage_password" != "$storage_password" ]; then
  echo "Key Storage and project-config encryption passwords differ; migrate both explicitly" >&2
  exit 1
fi

# Passwords generated here and by the Rundeck package are alphanumeric. Refuse an unsafe
# legacy value instead of writing an EnvironmentFile that systemd can interpret differently.
[[ "$storage_password" =~ ^[A-Za-z0-9]+$ ]] \
  || { echo "existing Key Storage password is not EnvironmentFile-safe; migrate it explicitly" >&2; exit 1; }
install -m 0440 -o root -g rundeck /dev/null "$STORAGE_PASSWORD_FILE"
printf 'RUNDECK_STORAGE_PASSWORD=%s\n' "$storage_password" > "$STORAGE_PASSWORD_FILE"
cred_set RUNDECK_STORAGE_PASSWORD "$storage_password"
unset storage_password
unset legacy_storage_password
unset legacy_config_storage_password

# Own the complete converter declaration. Leaving the package's inline password beside
# the environment source retains a second secret in ordinary configuration.
sed -i '/^rundeck\.storage\.converter\.1\./d' /etc/rundeck/rundeck-config.properties
cat >> /etc/rundeck/rundeck-config.properties <<'EOF'
rundeck.storage.converter.1.type=aes-gcm-encryption
rundeck.storage.converter.1.path=keys
rundeck.storage.converter.1.config.passwordEnvVarName=RUNDECK_STORAGE_PASSWORD
EOF

sed -i '/^rundeck\.config\.storage\.converter\.1\./d' /etc/rundeck/rundeck-config.properties
cat >> /etc/rundeck/rundeck-config.properties <<'EOF'
rundeck.config.storage.converter.1.type=aes-gcm-encryption
rundeck.config.storage.converter.1.path=projects
rundeck.config.storage.converter.1.config.passwordEnvVarName=RUNDECK_STORAGE_PASSWORD
EOF

mkdir -p /etc/systemd/system/rundeckd.service.d
cat > /etc/systemd/system/rundeckd.service.d/storage-password.conf <<'EOF'
[Service]
EnvironmentFile=/etc/rundeck/.storage-password
EOF

# Jobs inherit the service environment, so pin the locale for the ansible plugin too.
cat > /etc/systemd/system/rundeckd.service.d/locale.conf <<EOF
[Service]
Environment=LANG=en_US.UTF-8
Environment=LC_ALL=en_US.UTF-8
EOF
systemctl daemon-reload

# -- admin password -------------------------------------------------------------
# Only replace the shipped admin:admin. A re-run must not lock the operator out.
if grep -q '^admin:admin,' /etc/rundeck/realm.properties; then
  say "replacing default admin password"
  # Every producer here is BOUNDED, and nothing exits early. The obvious spelling,
  # `tr -dc ... </dev/urandom | head -c 28`, cannot be used under `set -euo pipefail`:
  # /dev/urandom never ends, so `head` exits at 28 bytes, `tr`'s next write takes SIGPIPE,
  # and pipefail hands that 141 to set -e — killing this script at the password step with
  # everything after it (venv, clone, config, jobs, Vaultwarden) silently unrun.
  RD_ADMIN_PW_POOL="$(head -c 96 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
  RD_ADMIN_PW="${RD_ADMIN_PW_POOL:0:28}"
  [ "${#RD_ADMIN_PW}" -eq 28 ] || { echo "could not generate an admin password" >&2; exit 1; }
  cp -n /etc/rundeck/realm.properties /etc/rundeck/realm.properties.orig
  sed -i "s|^admin:admin,|admin:${RD_ADMIN_PW},|" /etc/rundeck/realm.properties
  chown root:rundeck /etc/rundeck/realm.properties
  chmod 0640 /etc/rundeck/realm.properties
  cred_set RUNDECK_URL "$RD_URL"
  cred_set RUNDECK_ADMIN_USER admin
  cred_set RUNDECK_ADMIN_PASSWORD "$RD_ADMIN_PW"
else
  say "admin password already customised — leaving it alone"
fi

# -- ansible venv ---------------------------------------------------------------
# Debian 13 ships Python 3.13. This matters: community.proxmox 2.0.0 needs ansible-core
# >= 2.17, which needs a Python 3.11+ controller — the reason this cannot run on Debian 11.
say "ansible venv at $VENV_DIR"
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q --upgrade pip wheel
# PyYAML is not optional: scripts/config-doctor.sh, redact-config.sh and
# with-proxmox-env.sh all parse config/*.yml with this interpreter before Ansible starts.
# netaddr is not optional either: every guest this platform creates gets its address from
# tasks/network/generate-ip.yml, which filters through ansible.utils.ipaddr — and that
# filter is a thin wrapper over netaddr. Without it the very first provisioning task of the
# very first app deploy fails with "Failed to import the required Python library (netaddr)".
"$VENV_DIR/bin/pip" install -q "$ANSIBLE_CORE_SPEC" proxmoxer requests PyYAML netaddr
say "$("$VENV_DIR/bin/ansible" --version | head -1)"

# -- repo clone -----------------------------------------------------------------
say "repo at $REPO_DIR"
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" fetch --all -q
  git -C "$REPO_DIR" checkout -q "$REPO_BRANCH"
  git -C "$REPO_DIR" reset --hard -q "origin/${REPO_BRANCH}"
else
  git clone -q --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
fi
say "at $(git -C "$REPO_DIR" log --oneline -1)"

# -- the platform's own SSH identity --------------------------------------------
# Ansible connects to every guest this platform creates with this key. Generating it
# here rather than reusing the node's root key means the lab's guests trust exactly one
# identity, held by exactly one host, and revoking it revokes only the platform.
if [ ! -f "$LAB_SSH_KEY" ]; then
  if [ -f "$LAB_ETC/state/vault-mode" ] && [ -f "${LAB_SSH_KEY}.pub" ]; then
    # The absent private half is the expected post-cutover state. Generating a replacement
    # here would rotate the runner identity, strand every guest that trusts the old public
    # key, and overwrite the canonical Vaultwarden item during Key Storage staging below.
    say "platform SSH private key is held in Vaultwarden — preserving the existing public identity"
  else
    say "generating the platform SSH identity at $LAB_SSH_KEY"
    install -d -m 0700 -o rundeck -g rundeck "$(dirname "$LAB_SSH_KEY")"
    ssh-keygen -q -t ed25519 -N '' -C 'homelab-infra platform key' -f "$LAB_SSH_KEY"
    chown rundeck:rundeck "$LAB_SSH_KEY" "${LAB_SSH_KEY}.pub"
    chmod 0600 "$LAB_SSH_KEY"
  fi
else
  say "platform SSH identity already present"
fi

# -- lab-run wiring -------------------------------------------------------------
# lab-run.sh itself SHIPS IN THE REPO and arrives via the clone — it is never installed
# onto the host by hand. What lives on the host is the one env file that tells it where
# the checkout, the venv and the tracked branch are, plus a symlink so every job step is
# a single `lab-run <playbook>` call with no path in it.
#
# Writing this file is also what ARMS the per-run checkout refresh: lab-run.sh defaults
# LAB_REFRESH to 0 unless this file exists, so the `git reset --hard` it performs can
# only ever happen on a runner this script built.
say "lab-run wiring at $LAB_ETC"
install -d -m 0755 "$LAB_ETC"
cat > "$LAB_ETC/lab-run.env" <<EOF
# Written by rundeck/bootstrap-rundeck.sh. Read by ansible/scripts/lab-run.sh.
# These are the only paths that live on this host; everything else comes from the repo.
LAB_REPO=$REPO_DIR
LAB_VENV=$VENV_DIR
LAB_BRANCH=$REPO_BRANCH
RUNDECK_URL=$RD_URL
RUNDECK_PROJECT=$RD_PROJECT
# 1 = refresh the checkout to origin/\$LAB_BRANCH before every job, so a commit pushed to
# the repo is executed by the next click with no human action. 0 pins the on-disk tree.
LAB_REFRESH=1
# 1 = run config-doctor before every job, so a missing key fails at the front door.
LAB_DOCTOR=1
# Dedicated identity generated above. lab-run exports it as
# ANSIBLE_PRIVATE_KEY_FILE for guest SSH and node-delegated pct/qm waits.
LAB_SSH_KEY=$LAB_SSH_KEY
EOF
chmod 0644 "$LAB_ETC/lab-run.env"
install -d -m 0700 -o rundeck -g rundeck "$LAB_ETC/state"
ln -sfn "$REPO_DIR/ansible/scripts/lab-run.sh" /usr/local/bin/lab-run
say "installed /usr/local/bin/lab-run -> $REPO_DIR/ansible/scripts/lab-run.sh"

# -- collections ----------------------------------------------------------------
# Installed into the rundeck user's default collections path so jobs resolve them with no
# ANSIBLE_COLLECTIONS_PATH set. Versions come from the repo, which is the single source of truth.
say "collections from ansible/requirements.yml"
install -d -o rundeck -g rundeck /var/lib/rundeck/.ansible
sudo -u rundeck HOME=/var/lib/rundeck \
  "$VENV_DIR/bin/ansible-galaxy" collection install \
  -r "$REPO_DIR/ansible/requirements.yml" >/dev/null
sudo -u rundeck HOME=/var/lib/rundeck \
  "$VENV_DIR/bin/ansible-galaxy" collection list 2>/dev/null \
  | grep -E 'community.proxmox|ansible.utils|community.docker|community.general' \
  | sed 's/^/    /'

# -- ownership + service --------------------------------------------------------
# config/ is created here, owned by rundeck: Configure App writes into it as the job
# user, and it must survive `git reset --hard` (it is gitignored, so it does).
install -d -o rundeck -g rundeck -m 0750 "$REPO_DIR/config" "$REPO_DIR/config/apps" \
  "$REPO_DIR/config/.generated" "$REPO_DIR/config/.backups" "$REPO_DIR/config/apps/.backups" \
  "$REPO_DIR/artifacts"
chown -R rundeck:rundeck "$REPO_DIR" "$VENV_DIR" /var/lib/rundeck/.ansible
systemctl enable rundeckd >/dev/null 2>&1 || true
systemctl restart rundeckd
GUEST

# ── Author the config ──────────────────────────────────────────────────────────
# The direct answer to "nothing creates config/". Everything discoverable was discovered
# above; what remains genuinely needs a human, and each answer also reads an environment
# variable so the scripted path stays non-interactive.
log "Author config/"

CONFIG_PROXMOX="$REPO_DIR/config/proxmox.yml"
CONFIG_INFRA="$REPO_DIR/config/infrastructure.yml"

if ct_file_exists "$CONFIG_PROXMOX" && ct_file_exists "$CONFIG_INFRA" && [ "${FORCE_CONFIG:-0}" != "1" ]; then
  info "config/proxmox.yml and config/infrastructure.yml already exist — leaving them alone"
  info "re-author them with FORCE_CONFIG=1"
else
  # Default the lab's guest network to the container's own network. In the overwhelmingly
  # common single-subnet homelab that is simply correct, and where it is not, the prompt
  # is right there.
  DEFAULT_CIDR="$(net_addr "$CT_IP")"

  info "answer the lab and first-owner questions; everything else was discovered"
  ask LAB_DOMAIN      "Lab domain (apps are published as <app>.<domain>)"     ""
  ask VAULTWARDEN_OWNER_EMAIL "First Vaultwarden owner email"                 ""
  ask VAULTWARDEN_AUTOMATION_EMAIL "Dedicated automation account email"       "homelab-infra@$LAB_DOMAIN"
  ask LAB_NET_CIDR    "Guest network CIDR"                                    "$DEFAULT_CIDR"
  ask LAB_NET_GATEWAY "Guest network gateway"                                 "$CT_GW"
  ask LAB_NET_DNS     "DNS server for guests"                                 "$CT_GW"
  ask LAB_TIMEZONE    "Timezone for guests"                                   "$NODE_TZ"
  ask LAB_IP_OFFSET   "Start allocating guest IPs at .N"                      "10"

  info "provider choices — each may be 'none'"
  ask LAB_REVERSE_PROXY "Reverse proxy (caddy | nginx | none)"                "caddy"
  if [ "$LAB_REVERSE_PROXY" = "caddy" ]; then
    LAB_ACME_DNS_PROVIDER=cloudflare
    info "Caddy ACME DNS provider = cloudflare"
    ask_secret CLOUDFLARE_API_TOKEN "Cloudflare API token (Zone Read + DNS Edit for the lab zone)"
  fi
  ask LAB_SSO           "SSO (authentik | none)"                              "authentik"
  ask LAB_NOTIFICATIONS "Notifications (ntfy | gotify | discord | none)"      "ntfy"
  ask LAB_DNS           "DNS provider (pihole | adguard | opnsense | none)"   "none"
  if [ "$LAB_DNS" != "none" ]; then
    ask LAB_DNS_HOST    "DNS provider address (it is usually not a guest we created)" "$CT_GW"
  fi
  ask LAB_BACKUP_PATH   "PBS datastore path"                                  "/mnt/backup"

  # The platform's public key, read back out of the container it was generated in.
  LAB_SSH_PUBKEY="$(in_ct cat "${LAB_SSH_KEY}.pub" 2>/dev/null || true)"
  [ -n "$LAB_SSH_PUBKEY" ] || die "the platform SSH key was not generated at ${LAB_SSH_KEY}.pub"

  # Every cluster node's address, as YAML lines ready to nest under `nodes:`. A single-node
  # install still reports itself here, so there is no special case. If the query fails,
  # fall back to this node alone — register-nodes.yml also falls back to api_host.
  PVE_NODE_MAP="$(pvesh get /cluster/status --output-format json 2>/dev/null \
    | tr '{' '\n' \
    | sed -n 's/.*"ip":"\([^"]*\)".*"name":"\([^"]*\)".*/    \2: "\1"/p')"
  if [ -z "$PVE_NODE_MAP" ]; then
    PVE_NODE_MAP="    ${PVE_NODE}: \"${PVE_API_HOST}\""
    warn "could not read /cluster/status — recording only this node in proxmox.nodes"
  fi
  info "cluster nodes:"
  printf '%s\n' "$PVE_NODE_MAP" | sed 's/^    /        /'

  STAGE="$(newtmp)"

  # NOTE the absent api_token_secret. That is the point: the secret is minted below and
  # written to Key Storage, and with-proxmox-env.sh and load-user-vars.yml read
  # PROXMOX_API_TOKEN from the environment instead. This file carries shape, not secret,
  # which is what lets Get Config hand it around and lets a human read it in review.
  cat > "$STAGE/proxmox.yml" <<EOF
---
# Proxmox connection and global network config.
#
# Written by rundeck/bootstrap-rundeck.sh on $(date -Is) from what the node
# could discover plus the prompts it could not. Safe to edit by hand.
#
# There is deliberately NO api_token_secret here. The token is minted by the bootstrap
# script and stored in Rundeck Key Storage / the Semaphore environment; the platform
# reads it from PROXMOX_API_TOKEN. Keeping shape and secret apart is what lets this file
# be read, diffed, copied and reviewed.

proxmox:
  api_host: "$PVE_API_HOST"
  api_port: 8006
  node: "$PVE_NODE"
  api_user: "$PVE_USER"
  api_token_id: "$PVE_TOKEN_NAME"
  # api_token_secret: supplied as the PROXMOX_API_TOKEN environment variable

  # Storage every guest this platform creates lands on. Discovered above as the first
  # active storage advertising content type 'rootdir' — the type a container rootfs needs.
  # Lab-wide on purpose: which pool holds a guest is a fact about this node, not about the
  # app, so no app-default pins it. An app that genuinely needs its own pool sets
  # proxmox.disk_volume.storage (LXC) or proxmox.vm.storage (VM) in its instance file.
  storage: "$CT_STORAGE"

  # Node name -> address, for every node in the cluster. The provisioning tasks reach
  # node-local pct/qm over SSH with \`delegate_to: <node name>\`, and a Proxmox node name is
  # not resolvable on its own: the dynamic inventory gives nodes no ansible_host, and a
  # resolver has no reason to know them. tasks/proxmox/register-nodes.yml turns this map
  # into addressable hosts. Discovered from \`pvesh get /cluster/status\`.
  nodes:
$PVE_NODE_MAP

networks:
  default:
    cidr: "$LAB_NET_CIDR"
    gateway: "$LAB_NET_GATEWAY"
    dns_servers:
      - "$LAB_NET_DNS"
    bridge: "$CT_BRIDGE"
    vlan: 0
    ip_offset: $LAB_IP_OFFSET

ansible:
  ssh_user: root
  ssh_public_key: "$LAB_SSH_PUBKEY"
  timezone: "$LAB_TIMEZONE"
EOF

  {
    cat <<EOF
---
# Platform service role declarations — provider choices and instance names only.
# Endpoints and tokens are written by bootstrap into config/.generated/facts.yml.
#
# Written by rundeck/bootstrap-rundeck.sh on $(date -Is). Safe to edit by hand.

domain: "$LAB_DOMAIN"

reverse_proxy:
  provider: $LAB_REVERSE_PROXY
EOF
    if [ "$LAB_REVERSE_PROXY" != "none" ]; then
      echo "  instance: $LAB_REVERSE_PROXY"
      echo "  internal_cidrs:"
      echo "    - \"$LAB_NET_CIDR\""
    fi
    if [ "$LAB_REVERSE_PROXY" = "caddy" ]; then
      echo "  dns_challenge:"
      echo "    provider: $LAB_ACME_DNS_PROVIDER"
    fi
    cat <<EOF

sso:
  provider: $LAB_SSO
EOF
    [ "$LAB_SSO" != "none" ] && echo "  instance: $LAB_SSO"
    cat <<EOF

notifications:
  provider: $LAB_NOTIFICATIONS
EOF
    [ "$LAB_NOTIFICATIONS" != "none" ] && echo "  instance: $LAB_NOTIFICATIONS"
    cat <<EOF

dns:
  provider: $LAB_DNS
EOF
    [ "$LAB_DNS" != "none" ] && echo "  host: ${LAB_DNS_HOST}"
    cat <<EOF

backups:
  datastore_path: "$LAB_BACKUP_PATH"

# vaultwarden.admin_token is produced by bootstrap step 1, not authored here.
vaultwarden:
  instance: vaultwarden
  owner_email: "$VAULTWARDEN_OWNER_EMAIL"
  automation_email: "$VAULTWARDEN_AUTOMATION_EMAIL"
EOF
  } > "$STAGE/infrastructure.yml"

  push_file "$STAGE/proxmox.yml"        "$CONFIG_PROXMOX" 0640
  push_file "$STAGE/infrastructure.yml" "$CONFIG_INFRA"   0640
  in_ct chown rundeck:rundeck "$CONFIG_PROXMOX" "$CONFIG_INFRA"
  info "wrote config/proxmox.yml and config/infrastructure.yml"
fi

# Resolve the enrollment metadata on both fresh and converged runs. These values
# are identifiers, not credentials; master passwords never enter this script.
LAB_DOMAIN="$(in_ct "$VENV_DIR/bin/python3" -c 'import sys,yaml; print((yaml.safe_load(open(sys.argv[1])) or {}).get("domain", ""))' "$CONFIG_INFRA")"
VAULTWARDEN_OWNER_EMAIL="${VAULTWARDEN_OWNER_EMAIL:-$(in_ct "$VENV_DIR/bin/python3" -c 'import sys,yaml; print(((yaml.safe_load(open(sys.argv[1])) or {}).get("vaultwarden") or {}).get("owner_email", ""))' "$CONFIG_INFRA")}"
VAULTWARDEN_AUTOMATION_EMAIL="${VAULTWARDEN_AUTOMATION_EMAIL:-$(in_ct "$VENV_DIR/bin/python3" -c 'import sys,yaml; print(((yaml.safe_load(open(sys.argv[1])) or {}).get("vaultwarden") or {}).get("automation_email", ""))' "$CONFIG_INFRA")}"
[ -n "$VAULTWARDEN_AUTOMATION_EMAIL" ] || VAULTWARDEN_AUTOMATION_EMAIL="homelab-infra@$LAB_DOMAIN"
in_ct sh -c "grep -q '^BW_SERVER=' '$LAB_ETC/lab-run.env' || printf '%s\\n' 'BW_SERVER=https://vaultwarden.$LAB_DOMAIN' >> '$LAB_ETC/lab-run.env'"

# A converged pre-cutover rerun keeps the authored provider choice, but still needs
# its temporary credential to reconcile Caddy and restage Key Storage. The guided
# bootstrap currently knows how to collect Cloudflare's option schema; manually
# authored providers retain their own options and use their normal recovery path.
LAB_REVERSE_PROXY="${LAB_REVERSE_PROXY:-$(in_ct "$VENV_DIR/bin/python3" -c 'import sys,yaml; print(((yaml.safe_load(open(sys.argv[1])) or {}).get("reverse_proxy") or {}).get("provider", "none"))' "$CONFIG_INFRA")}"
LAB_ACME_DNS_PROVIDER="${LAB_ACME_DNS_PROVIDER:-$(in_ct "$VENV_DIR/bin/python3" -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])) or {}; print((((d.get("reverse_proxy") or {}).get("dns_challenge") or {}).get("provider", "")))' "$CONFIG_INFRA")}"
if [ "$DEPLOY_VAULTWARDEN" = "1" ] \
   && ! ct_file_exists "$LAB_ETC/state/vault-mode" \
   && [ "$LAB_REVERSE_PROXY" = "caddy" ] \
   && [ "$LAB_ACME_DNS_PROVIDER" = "cloudflare" ] \
   && [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  ask_secret CLOUDFLARE_API_TOKEN \
    "Cloudflare API token (Zone Read + DNS Edit for the lab zone)"
fi

# ── Describe the runner as an instance ─────────────────────────────────────────
# The runner is a guest this platform now manages, so it gets an instance file like any
# other. Nothing deploys from it — the script owns the runner's creation, because it must:
# it runs before any of this exists. What this records is the description that used to
# exist only as a comment in a backlog document.
log "Describe the runner"
CONFIG_RUNNER="$REPO_DIR/config/apps/rundeck.yml"
STAGE2="$(newtmp)"
cat > "$STAGE2/rundeck.yml" <<EOF
---
# The runner describing itself.
#
# Written by rundeck/bootstrap-rundeck.sh on $(date -Is). This host was created
# by that script, not by an app playbook — there is no roles/rundeck/ and no Deploy
# Rundeck job, because the script has to run before any of that exists.
#
# It is here so the platform can name the host it is running on: the guest is tagged
# $MANAGED_TAGS, so PBS backs it up and Lab Status reports it, and this file is
# where its vmid, address and paths are recorded.

proxmox:
  vmid: $VMID
  hostname: "$CT_HOSTNAME"
  node: "$PVE_NODE"
  cores: $CT_CORES
  memory: $CT_MEMORY
  disk: $CT_DISK
  storage: "$CT_STORAGE"
  ip: "$CT_IP"
  gateway: "$CT_GW"
  bridge: "$CT_BRIDGE"

app:
  port: $RD_PORT
  service_name: rundeckd
  checkout_path: "$REPO_DIR"
  venv_path: "$VENV_DIR"

routing:
  # The runner is not published through the reverse proxy by default — it is the thing
  # that operates the lab, not part of it. Set proxy/identity here if you want it routed.
  identity: none
EOF
push_file "$STAGE2/rundeck.yml" "$CONFIG_RUNNER" 0640
in_ct chown rundeck:rundeck "$CONFIG_RUNNER"
info "wrote config/apps/rundeck.yml"

# The provisioning tasks use the Proxmox API for create/update, then delegate
# node-local `pct`/`qm` readiness checks to the PVE node. Authorize the platform's
# dedicated runner identity for that existing contract. The exact key is appended
# idempotently and no password or host key is copied.
log "Authorize the automation runner on this Proxmox node"
LAB_SSH_PUBKEY="$(in_ct cat "${LAB_SSH_KEY}.pub" 2>/dev/null || true)"
[ -n "$LAB_SSH_PUBKEY" ] || die "the platform SSH key was not generated at ${LAB_SSH_KEY}.pub"
install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
grep -qxF "$LAB_SSH_PUBKEY" /root/.ssh/authorized_keys \
  || printf '%s\n' "$LAB_SSH_PUBKEY" >> /root/.ssh/authorized_keys
info "platform runner key is authorized for node-local pct/qm waits"

# ── Proxmox credential ─────────────────────────────────────────────────────────
# Node root can issue the platform's credential, so nothing here asks a human for one.
log "Proxmox credential"

# The privilege set is derived from what the playbooks actually call:
#   VM.*                LXC and VM create / configure / power / destroy / clone / snapshot
#                       (tasks/proxmox/lxc-create.yml, vm-create.yml, vm-clone.yml),
#                       VM.Audit for the dynamic inventory and Lab Status
#   Datastore.*         rootfs and disk allocation, template download (pveam), and
#                       Datastore.Allocate to register PBS as a storage backend
#                       (tasks/bootstrap/configure-pbs.yml)
#   Sys.Audit/Modify    node facts, the bridge on a new NIC, and the cluster-level vzdump
#                       backup job configure-pbs.yml creates
#   SDN.*               bridge selection on PVE 8+
#   Pool.Audit          guest enumeration by the inventory plugin
#
# This is a real API-credential reduction from root@pam — no user, realm, permission or
# ACL management — but be clear-eyed: Sys.Modify at / is broad. It is required because
# creating a storage backend and a cluster backup job are cluster-configuration writes.
# The separate SSH identity authorized above still has the node-root command channel the
# existing pct/qm delegate tasks require; that is not a property of this API token.
PVE_PRIVS_WANTED="Datastore.Allocate,Datastore.AllocateSpace,Datastore.AllocateTemplate,Datastore.Audit,Pool.Audit,SDN.Audit,SDN.Use,Sys.Audit,Sys.Console,Sys.Modify,VM.Allocate,VM.Audit,VM.Backup,VM.Clone,VM.Config.CDROM,VM.Config.CPU,VM.Config.Cloudinit,VM.Config.Disk,VM.Config.HWType,VM.Config.Memory,VM.Config.Network,VM.Config.Options,VM.Console,VM.Migrate,VM.PowerMgmt,VM.Snapshot,VM.Snapshot.Rollback"

# The privilege VOCABULARY moves between PVE releases: SDN.* arrived in 8, VM.Monitor was
# removed in 9. `pveum role add` rejects the whole list on the first unknown name, so a
# hardcoded set makes this script version-locked, and a per-privilege fallback needs a new
# branch for every future change. Intersect with what THIS node accepts instead — the
# Administrator role holds every privilege the node knows, so it is the vocabulary itself.
PVE_PRIVS_SUPPORTED="$(pvesh get /access/roles/Administrator --output-format json 2>/dev/null \
  | tr ',' '\n' | tr -d '{}" ' | sed 's/:1$//' \
  | grep -E '^[A-Za-z]+\.[A-Za-z.]+$' || true)"

PVE_PRIVS=""
PVE_PRIVS_DROPPED=""
if [ -n "$PVE_PRIVS_SUPPORTED" ]; then
  for _priv in $(printf '%s' "$PVE_PRIVS_WANTED" | tr ',' ' '); do
    if printf '%s\n' "$PVE_PRIVS_SUPPORTED" | grep -qxF "$_priv"; then
      PVE_PRIVS="${PVE_PRIVS:+${PVE_PRIVS},}${_priv}"
    else
      PVE_PRIVS_DROPPED="${PVE_PRIVS_DROPPED:+${PVE_PRIVS_DROPPED} }${_priv}"
    fi
  done
  if [ -n "$PVE_PRIVS_DROPPED" ]; then
    warn "this PVE does not define: $PVE_PRIVS_DROPPED — dropped from $PVE_ROLE"
  fi
else
  warn "could not read this node's privilege vocabulary — requesting the full set"
  PVE_PRIVS="$PVE_PRIVS_WANTED"
fi
[ -n "$PVE_PRIVS" ] || die "no usable privileges resolved for role $PVE_ROLE"

if pveum role list --output-format json 2>/dev/null | grep -q "\"roleid\":\"${PVE_ROLE}\""; then
  info "role $PVE_ROLE exists — updating its privileges"
  pveum role modify "$PVE_ROLE" --privs "$PVE_PRIVS" >/dev/null \
    || die "could not set privileges on role $PVE_ROLE"
else
  info "creating role $PVE_ROLE"
  pveum role add "$PVE_ROLE" --privs "$PVE_PRIVS" >/dev/null \
    || die "could not create role $PVE_ROLE"
fi

if pveum user list --output-format json 2>/dev/null | grep -q "\"userid\":\"${PVE_USER}\""; then
  info "user $PVE_USER exists"
else
  info "creating user $PVE_USER"
  pveum user add "$PVE_USER" --comment "homelab-infra platform automation" >/dev/null
fi

# Propagating from / is what lets the platform create guests on any node and allocate on
# any storage without this script having to enumerate them.
pveum acl modify / --users "$PVE_USER" --roles "$PVE_ROLE" >/dev/null
info "granted $PVE_ROLE on / to $PVE_USER"

PVE_TOKEN_SECRET=""
TOKEN_EXISTS=0
if pveum user token list "$PVE_USER" --output-format json 2>/dev/null \
     | grep -q "\"tokenid\":\"${PVE_TOKEN_NAME}\""; then
  TOKEN_EXISTS=1
fi

if [ "$TOKEN_EXISTS" -eq 1 ] && [ "$ROTATE_PROXMOX_TOKEN" = "1" ]; then
  info "rotating token ${PVE_USER}!${PVE_TOKEN_NAME} (ROTATE_PROXMOX_TOKEN=1)"
  pveum user token remove "$PVE_USER" "$PVE_TOKEN_NAME" >/dev/null
  TOKEN_EXISTS=0
fi

if [ "$TOKEN_EXISTS" -eq 0 ]; then
  info "minting token ${PVE_USER}!${PVE_TOKEN_NAME}"
  # Captured in-process and never written to a file on the node: it goes straight into
  # Key Storage and the runner's root-owned secrets env below.
  TOKEN_JSON="$(pveum user token add "$PVE_USER" "$PVE_TOKEN_NAME" --privsep 0 --output-format json)"
  PVE_TOKEN_SECRET="$(printf '%s' "$TOKEN_JSON" | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  [ -n "$PVE_TOKEN_SECRET" ] || die "could not read the token secret out of the pveum response"
  info "minted — the secret is displayed once and is now held only in this process"
else
  info "token ${PVE_USER}!${PVE_TOKEN_NAME} already exists — keeping it"
  info "a token secret cannot be re-read; rotate with ROTATE_PROXMOX_TOKEN=1"
fi

# A runner created before the AES-GCM converter may still have the existing token in its
# temporary Seed file. Recover it without printing so ks_put below rewrites the known
# Key Storage entry through the converter. After cutover this file is gone and the old
# Key Storage path is gone with it.
for _seed_env in "$LAB_SEED_ENV" "$LAB_LEGACY_SEED_ENV"; do
  [ -z "$PVE_TOKEN_SECRET" ] || break
  ct_file_exists "$_seed_env" || continue
  PVE_TOKEN_SECRET="$(in_ct sed -n 's/^PROXMOX_API_TOKEN=//p' "$_seed_env")"
  [ -z "$PVE_TOKEN_SECRET" ] \
    || info "existing Proxmox seed recovered for AES-GCM Key Storage rewrite"
done

# ── Wait for Rundeck ───────────────────────────────────────────────────────────
log "Wait for Rundeck to accept connections"
for i in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$RD_URL/" 2>/dev/null || true)"
  case "${code:-000}" in
    200|302|303|401|403) info "up after ${i} attempt(s) (HTTP $code)"; break ;;
  esac
  sleep 5
done
code="$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$RD_URL/" 2>/dev/null || true)"
case "${code:-000}" in
  200|302|303|401|403) : ;;
  *) die "Rundeck did not come up at $RD_URL (last HTTP ${code:-000}); check: pct exec $VMID -- tail -50 /var/log/rundeck/service.log" ;;
esac

# ── API token ──────────────────────────────────────────────────────────────────
log "Rundeck API token"
RD_ADMIN_PW="$(in_ct sh -c "sed -n 's/^RUNDECK_ADMIN_PASSWORD=//p' $CRED_FILE 2>/dev/null" || true)"
RD_TOKEN="$(in_ct sh -c "sed -n 's/^RUNDECK_API_TOKEN=//p' $CRED_FILE 2>/dev/null" || true)"

if [ -z "$RD_TOKEN" ]; then
  if [ -z "$RD_ADMIN_PW" ]; then
    warn "no stored admin password at ${CRED_FILE} in the container — skipping token issue"
    info "create one in the UI under: admin -> Profile -> User API Tokens"
  else
    # EVERY call below both reads AND writes the jar (-b and -c together). Rundeck rotates
    # JSESSIONID on API requests, so a call given only -b sends whatever the previous
    # response superseded: the session is silently stale and the API answers
    # `"(unauthenticated) is not authorized"` even though the UI login plainly worked.
    # It is the intervening GET that rotates the cookie the POST then needs, which is why
    # the failure looked like "the token endpoint is forbidden" rather than "the cookie
    # is out of date". Dropping -c from any one of these reintroduces the bug.
    COOKIE="$(mktemp "$TMPROOT/cookie.XXXXXX")"
    curl -s -c "$COOKIE" -o /dev/null -m 15 "$RD_URL/user/login"
    curl -s -b "$COOKIE" -c "$COOKIE" -o /dev/null -m 15 \
      -d "j_username=admin" --data-urlencode "j_password=${RD_ADMIN_PW}" \
      "$RD_URL/j_security_check"

    EXISTING="$(curl -s -b "$COOKIE" -c "$COOKIE" -H 'Accept: application/json' -m 15 \
      "$RD_URL/api/58/tokens/admin" 2>/dev/null | grep -c '"name":"homelab-infra"' || true)"

    if [ "${EXISTING:-0}" -gt 0 ]; then
      warn "a token named 'homelab-infra' exists but its secret is not in $CRED_FILE"
      info "revoke it in the UI and re-run this script to issue a readable one"
    else
      RESP="$(curl -s -b "$COOKIE" -c "$COOKIE" -m 20 -X POST \
        -H 'Content-Type: application/json' -H 'Accept: application/json' \
        -d '{"user":"admin","roles":["*"],"duration":"0","name":"homelab-infra"}' \
        "$RD_URL/api/58/tokens/admin")"
      RD_TOKEN="$(printf '%s' "$RESP" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')"
      if [ -n "$RD_TOKEN" ]; then
        info "issued non-expiring token 'homelab-infra'"
        in_ct sh -c "grep -q '^RUNDECK_API_TOKEN=' $CRED_FILE 2>/dev/null \
          || echo 'RUNDECK_API_TOKEN=${RD_TOKEN}' >> $CRED_FILE"
      else
        warn "token request failed"
      fi
    fi
  fi
else
  info "reusing the stored API token"
fi

# The .env the repo's own tooling reads, written into the checkout this script just made
# rather than telling the operator to copy it across by hand.
if [ -n "$RD_TOKEN" ]; then
  STAGE3="$(newtmp)"
cat > "$STAGE3/env" <<EOF
# Written by rundeck/bootstrap-rundeck.sh. Gitignored.
RUNDECK_URL=$RD_URL
RUNDECK_PROJECT=$RD_PROJECT
EOF
  push_file "$STAGE3/env" "$REPO_DIR/.env" 0600
  in_ct chown rundeck:rundeck "$REPO_DIR/.env"
  info "wrote $REPO_DIR/.env"
fi

# ── Rundeck project, Key Storage, jobs ─────────────────────────────────────────
rd_api() {
  # rd_api <method> <path> [content-type] [body-file]
  local method="$1" path="$2" ctype="${3:-application/json}" body="${4:-}"
  if [ -n "$body" ]; then
    curl -s -m 60 -X "$method" -H "X-Rundeck-Auth-Token: $RD_TOKEN" \
      -H "Accept: application/json" -H "Content-Type: $ctype" \
      --data-binary "@$body" "$RD_URL/api/$RD_API/$path"
  else
    curl -s -m 60 -X "$method" -H "X-Rundeck-Auth-Token: $RD_TOKEN" \
      -H "Accept: application/json" "$RD_URL/api/$RD_API/$path"
  fi
}

if [ -z "$RD_TOKEN" ]; then
  warn "no Rundeck API token — skipping project creation, Key Storage and job import"
  warn "re-run this script once a token exists to finish the handover"
else
  log "Rundeck project '$RD_PROJECT'"
  if rd_api GET "project/$RD_PROJECT" | grep -q '"name"'; then
    info "project already exists"
  else
    STAGE4="$(newtmp)"
    printf '{"name":"%s","config":{"project.description":"homelab-infra — one click per app"}}\n' \
      "$RD_PROJECT" > "$STAGE4/project.json"
    if rd_api POST "projects" application/json "$STAGE4/project.json" | grep -q '"name"'; then
      info "created"
    else
      warn "project creation did not return a project — check $RD_URL manually"
    fi
  fi

  # ── Key Storage ──────────────────────────────────────────────────────────────
  # Two entries, both staged from values this script already holds. Nothing is pasted.
  log "Key Storage"
  ks_put() {
    # ks_put <path> <content-type> <body-file>
    local path="$1" ctype="$2" body="$3" method=POST code
    if curl -s -o /dev/null -w '%{http_code}' -m 20 \
         -H "X-Rundeck-Auth-Token: $RD_TOKEN" \
         "$RD_URL/api/$RD_API/storage/$path" | grep -q '^200$'; then
      method=PUT
    fi
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 20 -X "$method" \
      -H "X-Rundeck-Auth-Token: $RD_TOKEN" -H "Content-Type: $ctype" \
      --data-binary "@$body" "$RD_URL/api/$RD_API/storage/$path")"
    case "$code" in
      200|201) info "staged $path" ;;
      *)       warn "could not stage $path (HTTP $code)" ;;
    esac
  }

  STAGE5="$(newtmp)"
  printf '%s' "$RD_TOKEN" > "$STAGE5/rundeck-api-token"
  chmod 0600 "$STAGE5/rundeck-api-token"
  ks_put "keys/project/$RD_PROJECT/rundeck/api-token" \
    "application/x-rundeck-data-password" "$STAGE5/rundeck-api-token"

  if [ -n "$PVE_TOKEN_SECRET" ]; then
    printf '%s' "$PVE_TOKEN_SECRET" > "$STAGE5/proxmox-token"
    chmod 0600 "$STAGE5/proxmox-token"
    ks_put "keys/proxmox/api-token" "application/x-rundeck-data-password" "$STAGE5/proxmox-token"
  else
    info "keys/proxmox/api-token left as-is — the existing token's secret cannot be re-read"
  fi

  if [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
    printf '%s' "$CLOUDFLARE_API_TOKEN" > "$STAGE5/cloudflare-api-token"
    chmod 0600 "$STAGE5/cloudflare-api-token"
    ks_put "keys/project/$RD_PROJECT/bootstrap/cloudflare-api-token" \
      "application/x-rundeck-data-password" "$STAGE5/cloudflare-api-token"
  fi

  # Ansible needs the private half as a file to connect to guests with; Key Storage is
  # where Rundeck keeps it, and this is the copy that survives losing the container.
  if in_ct test -f "$LAB_SSH_KEY"; then
    in_ct cat "$LAB_SSH_KEY" > "$STAGE5/homelab-ssh"
    chmod 0600 "$STAGE5/homelab-ssh"
    ks_put "keys/rundeck/homelab-ssh" "application/octet-stream" "$STAGE5/homelab-ssh"
  fi

  # ── Jobs ─────────────────────────────────────────────────────────────────────
  # The REST API accepts the same YAML the `rd` CLI sends, so no CLI is required on the
  # node. uuidOption=preserve + dupeOption=update means re-importing updates the existing
  # jobs in place rather than duplicating them — job UUIDs in the repo are stable.
  #
  # Imported from the CLONE INSIDE THE CONTAINER, not from beside this script: the script
  # is usually scp'd to the node on its own, and the clone is the copy that is guaranteed
  # to be present and to match what the jobs will execute.
  log "Import jobs"
  in_ct env RD_URL="$RD_URL" RD_TOKEN="$RD_TOKEN" RD_PROJECT="$RD_PROJECT" \
    RD_API="$RD_API" REPO_DIR="$REPO_DIR" VENV_DIR="$VENV_DIR" bash -s <<'IMPORT'
set -euo pipefail
n=0; f=0
# The generated per-application jobs offer their instances as a dropdown read from these
# files. Write them before the import so the option provider resolves on the first visit;
# lab-run.sh rewrites them before and after every job afterwards.
"$VENV_DIR/bin/python3" "$REPO_DIR/ansible/scripts/app-instances.py" \
  --repo "$REPO_DIR" --out /var/lib/rundeck/app-instances
"$VENV_DIR/bin/python3" "$REPO_DIR/rundeck/render-job.py" --check "$REPO_DIR/rundeck/jobs"
for job in "$REPO_DIR"/rundeck/jobs/*.yaml; do
  [ -f "$job" ] || continue
  resp="$("$VENV_DIR/bin/python3" "$REPO_DIR/rundeck/render-job.py" "$job" | curl -s -m 60 -X POST \
    -H "X-Rundeck-Auth-Token: $RD_TOKEN" -H "Accept: application/json" \
    -H "Content-Type: application/yaml" --data-binary @- \
    "$RD_URL/api/$RD_API/project/$RD_PROJECT/jobs/import?fileformat=yaml&dupeOption=update&uuidOption=preserve")"
  if printf '%s' "$resp" | grep -q '"succeeded"'; then
    n=$((n+1))
  else
    f=$((f+1)); echo "    FAILED $(basename "$job"): $resp"
  fi
done
echo "    $n job(s) imported, $f failed"
IMPORT
fi

# ── Temporary Seed material, outside the checkout ─────────────────────────────
# These files exist only long enough to bring up Caddy/Vaultwarden and complete verified
# cutover. The marker makes recreated copies inert for every ordinary job.
log "Temporary Seed material"

# secrets.d/ holds every temporary Seed file, including the admin token generated by
# the first Vaultwarden deploy.
#
# Owned by the job user, not root, and created before anything is written into it: a
# playbook running as rundeck has to create a file here without sudo, and cutover has
# to remove it without sudo once the encrypted recovery copy and canonical item both
# verify. Unlinking a file requires write permission on its directory rather than on
# the file, so a root-owned directory would leave seed secrets on disk after cutover.
in_ct mkdir -p "$LAB_ETC/secrets.d"
in_ct chown rundeck:rundeck "$LAB_ETC/secrets.d"
in_ct chmod 0700 "$LAB_ETC/secrets.d"
info "created $LAB_ETC/secrets.d (0700 rundeck:rundeck) for temporary Seed material"

if [ -n "$PVE_TOKEN_SECRET" ]; then
  STAGE6="$(newtmp)"
  cat > "$STAGE6/proxmox.env" <<EOF
# Written by rundeck/bootstrap-rundeck.sh. Sourced by ansible/scripts/lab-run.sh.
# Outside the git checkout and outside config/ on purpose — see the script's header.
# The same value is in Rundeck Key Storage at keys/proxmox/api-token.
PROXMOX_API_TOKEN=$PVE_TOKEN_SECRET
EOF
  push_file "$STAGE6/proxmox.env" "$LAB_SEED_ENV" 0600
  in_ct chown rundeck:rundeck "$LAB_SEED_ENV"
  info "wrote $LAB_SEED_ENV (0600 rundeck:rundeck)"
else
  if ct_file_exists "$LAB_SEED_ENV" || ct_file_exists "$LAB_LEGACY_SEED_ENV"; then
    info "the temporary Seed file already holds the existing token"
  else
    warn "the Proxmox token exists but its secret is not on the runner"
    warn "re-run with ROTATE_PROXMOX_TOKEN=1 to mint one this script can store"
  fi
fi

# ── Record the runner in the service registry ──────────────────────────────────
# So playbooks and Lab Status can name the host they are running on.
log "Register the runner"
STAGE7="$(newtmp)"
cat > "$STAGE7/runner.yml" <<EOF
runner:
  provider: rundeck
  instance: rundeck
  host: "$RD_URL"
  vmid: $VMID
  node: "$PVE_NODE"
  checkout_path: "$REPO_DIR"
  venv_path: "$VENV_DIR"
  branch: "$REPO_BRANCH"
EOF
push_file "$STAGE7/runner.yml" /tmp/homelab-runner-key.yml 0600
in_ct env FACTS="$REPO_DIR/config/.generated/facts.yml" PATCH=/tmp/homelab-runner-key.yml \
  VENV_DIR="$VENV_DIR" bash -s <<'MERGE'
set -euo pipefail
# Merge rather than overwrite: facts.yml grows one role key at a time and already holds
# whatever earlier bootstrap steps wrote.
install -d -o rundeck -g rundeck -m 0750 "$(dirname "$FACTS")"
if [ -f "$FACTS" ]; then
  "$VENV_DIR/bin/python3" - "$FACTS" "$PATCH" <<'PY'
import sys, yaml
target, patch = sys.argv[1], sys.argv[2]
with open(target) as fh:
    data = yaml.safe_load(fh) or {}
with open(patch) as fh:
    data.update(yaml.safe_load(fh) or {})
with open(target, "w") as fh:
    yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)
PY
else
  cp "$PATCH" "$FACTS"
fi
chown rundeck:rundeck "$FACTS"; chmod 0600 "$FACTS"
rm -f "$PATCH"
MERGE
info "recorded the runner registry key in config/.generated/facts.yml"

# ── Preliminary Vaultwarden ───────────────────────────────────────────────────
# This is still Ansible-owned provisioning. Running it here simply moves the first
# app deployment into the host bootstrap so the script returns with the secret
# store online. apps/vaultwarden.yml refreshes dynamic inventory, creates the LXC
# when absent, and reuses `tag_vaultwarden` on every later run.
if [ "$DEPLOY_VAULTWARDEN" = "1" ] && ! ct_file_exists "$LAB_ETC/state/vault-mode"; then
  log "Seed phase: deploy Caddy, Vaultwarden and the HTTPS Vaultwarden route"
  if ! ct_file_exists "$LAB_SEED_ENV" && ! ct_file_exists "$LAB_LEGACY_SEED_ENV"; then
    die "cannot deploy Vaultwarden: $LAB_SEED_ENV is absent.
The Proxmox token secret cannot be re-read; re-run with ROTATE_PROXMOX_TOKEN=1
to mint a token the runner can use."
  fi

  [ "$LAB_REVERSE_PROXY" = "caddy" ] || die "first-owner enrollment requires Caddy HTTPS; infrastructure.reverse_proxy.provider is '$LAB_REVERSE_PROXY'"

  # Carry the secret on stdin, then expose it only in the child process environment.
  # pct/sudo command arguments and the Ansible extra-vars list remain non-secret.
  printf '%s\n' "${CLOUDFLARE_API_TOKEN:-}" | in_ct sudo -u rundeck env \
    HOME=/var/lib/rundeck LAB_SEED_MODE=1 LAB_REFRESH=0 LAB_DOCTOR=1 \
    ANSIBLE_PRIVATE_KEY_FILE="$LAB_SSH_KEY" bash -c '
      IFS= read -r CLOUDFLARE_API_TOKEN
      export CLOUDFLARE_API_TOKEN
      exec /usr/local/bin/lab-run playbooks/apps/caddy.yml -e instance=caddy
    '

  # The address every lab hostname has to resolve to. Read back from the registry key
  # the deploy just wrote, so the guidance below names a real IP rather than a shape.
  CADDY_ADDR="$(in_ct "$VENV_DIR/bin/python3" -c 'import re,sys,yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
print(re.sub(r"^https?://|:.*$", "", ((d.get("reverse_proxy") or {}).get("host") or "")))' \
    "$REPO_DIR/config/.generated/facts.yml" 2>/dev/null || true)"
  CADDY_ADDR="${CADDY_ADDR:-}"

  # Vaultwarden comes up behind an already-live proxy. Its wiring pass adds the
  # HTTPS route immediately, so the enrollment URL is the first supported entry.
  in_ct sudo -u rundeck env \
    HOME=/var/lib/rundeck LAB_SEED_MODE=1 LAB_REFRESH=0 LAB_DOCTOR=1 \
    ANSIBLE_PRIVATE_KEY_FILE="$LAB_SSH_KEY" \
    /usr/local/bin/lab-run playbooks/apps/vaultwarden.yml -e instance=vaultwarden
  info "Vaultwarden and Caddy are online; the HTTPS route is configured"

  # Preserve the generated admin token in encrypted Key Storage before asking a
  # human to enroll. The temporary sink remains until verified Vault cutover.
  if [ -n "$RD_TOKEN" ] && declare -F ks_put >/dev/null 2>&1; then
    STAGE8="$(newtmp)"
    in_ct sed -n 's/^VAULTWARDEN_ADMIN_TOKEN=//p' "$LAB_ETC/secrets.d/vaultwarden.env" > "$STAGE8/admin-token"
    chmod 0600 "$STAGE8/admin-token"
    if [ -s "$STAGE8/admin-token" ]; then
      ks_put "keys/project/$RD_PROJECT/vaultwarden-machine/admin-token" \
        "application/x-rundeck-data-password" "$STAGE8/admin-token"
    fi
  fi

  if [ -n "$VAULTWARDEN_OWNER_EMAIL" ]; then
    info "attempting first-owner invitations through the HTTPS admin facility"
    if in_ct curl -fsS --max-time 15 "https://vaultwarden.$LAB_DOMAIN/alive" >/dev/null 2>&1; then
      in_ct sudo -u rundeck env \
        HOME=/var/lib/rundeck LAB_SEED_MODE=1 LAB_REFRESH=0 LAB_DOCTOR=1 \
        VAULTWARDEN_OWNER_EMAIL="$VAULTWARDEN_OWNER_EMAIL" \
        VAULTWARDEN_AUTOMATION_EMAIL="$VAULTWARDEN_AUTOMATION_EMAIL" \
        /usr/local/bin/lab-run playbooks/maintenance/vaultwarden-enroll.yml
    else
      warn "https://vaultwarden.$LAB_DOMAIN is not reachable from the runner yet"
      info "Caddy is up${CADDY_ADDR:+ at $CADDY_ADDR} — the name has to resolve there and"
      info "the path to it on 443 has to be open before enrollment can run. Point"
      info "'vaultwarden.$LAB_DOMAIN'${CADDY_ADDR:+ -> $CADDY_ADDR} in your LAN resolver,"
      info "allow client subnets to reach it, then run the Vaultwarden Enrollment job."
      info "Certificate issuance uses DNS-01, so getting the certificate needs no public"
      info "record and no inbound WAN port. Publishing an app to the internet later is a"
      info "separate choice that does require inbound 443."
    fi
  else
    warn "no owner email is recorded; set VAULTWARDEN_OWNER_EMAIL and run Vaultwarden Enrollment"
  fi
elif [ "$DEPLOY_VAULTWARDEN" = "1" ]; then
  info "Vault mode is already active — skipping the preliminary Seed-only app phase"
else
  warn "DEPLOY_VAULTWARDEN=0 — runner created without the preliminary secret store"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
log "Done"
cat <<EOF
    Rundeck    $RD_URL   (project: $RD_PROJECT)
    Container  VMID $VMID ($CT_HOSTNAME) on $PVE_NODE, tagged $MANAGED_TAGS
    SSH        ssh root@${RD_HOST}
    Repo       $REPO_DIR   (tracking origin/$REPO_BRANCH, refreshed before every job)
    Ansible    $VENV_DIR/bin/ansible
    Config     $REPO_DIR/config/{proxmox.yml,infrastructure.yml,apps/rundeck.yml}
    Proxmox    $PVE_USER (role $PVE_ROLE), token secret in Key Storage
    Vaultwarden $([ "$DEPLOY_VAULTWARDEN" = "1" ] && printf '%s' 'deployed with Caddy; enrollment/cutover required' || printf '%s' 'skipped (runner-only mode)')
    Creds      pct exec $VMID -- cat $CRED_FILE

    NETWORK: every lab hostname is served by Caddy${CADDY_ADDR:+ at $CADDY_ADDR}. Lab
    hostnames must resolve there in your LAN resolver and client subnets must be allowed
    to reach it on 80/443. Certificate ISSUANCE uses DNS-01, so obtaining a certificate
    needs no public record and no inbound WAN port. Publishing an app to the internet is
    a separate and deliberate choice (routing.access: public) and does require inbound
    443 forwarded here. An existing internet-facing proxy keeps its own ports either way.

    NEXT: two Rundeck jobs, with one browser session between them. Open $RD_URL.

      1. DNS FIRST. Point vaultwarden.$LAB_DOMAIN at ${CADDY_ADDR:-the Caddy LXC} in your
         LAN resolver. Nothing below works until that name resolves.
      2. Run the "Vaultwarden Enrollment" JOB (Bootstrap group). It sends the two
         invitations and needs no input from you — its admin token is already staged.
         Skip it if the invitations above already succeeded.
      3. In a BROWSER at https://vaultwarden.$LAB_DOMAIN, register the owner address and
         the automation address. YOU CHOOSE both master passwords here; nothing in this
         project generates, stores or prints them. Then, as the owner, create the
         organization 'homelab-infra' and the collection 'platform-secrets', and grant
         the automation account access to that collection.
      4. Signed in as the automation account, open Settings > Security > Keys and view
         its API key. Put that client id and client secret, plus the master password you
         chose in step 3, into Rundeck Key Storage under
         keys/project/$RD_PROJECT/vaultwarden-machine/ as client-id, client-secret and
         master-password.
      5. Run the "Vaultwarden Cutover" JOB, then the "Bootstrap Platform" JOB. Bootstrap
         Platform reuses Caddy and Vaultwarden and deploys the remaining services.

    Config lives on this runner and is reachable from the UI in both directions —
    Configure App writes an instance file, Get Config reads the set back out,
    Config Doctor validates it. No SSH session is needed to read or change the
    lab definition.
EOF
