#!/usr/bin/env bash
# bootstrap-rundeck.sh — stand up the Rundeck UI layer for homelab-infra in a Proxmox LXC.
#
# Run this ON a Proxmox node, as root. It is idempotent: re-running converges an existing
# container instead of rebuilding it, and never rotates a password or token you already have.
#
#   ./bootstrap-rundeck.sh
#   VMID=13228 CT_IP=192.168.13.228/20 CT_GW=192.168.13.1 ./bootstrap-rundeck.sh
#
# What it produces:
#   - Unprivileged Debian 13 LXC with nesting enabled
#   - OpenJDK 21 + Rundeck 6.x (Rundeck 6 requires Java 17+)
#   - A random admin password (the Rundeck package ships admin:admin)
#   - A non-expiring Rundeck API token for automation
#   - ansible-core 2.18 in a venv, with the collections pinned in ansible/requirements.yml
#   - A clone of this repo at /var/lib/rundeck/homelab-infra
#
# Credentials are written inside the container to /root/.rundeck-bootstrap (0600) and echoed
# in the closing summary. Re-runs read that file back rather than issuing new secrets.
#
# NOT handled here (deliberate — revisit once apps and wiring land): creating the Rundeck
# project, importing rundeck/jobs/*.yaml over the git SCM plugin, and staging Key Storage
# entries for the Proxmox and Vaultwarden tokens.

set -euo pipefail

# ── Tunables ───────────────────────────────────────────────────────────────────
VMID="${VMID:-13228}"
CT_HOSTNAME="${CT_HOSTNAME:-pve-rundeck-4}"
CT_IP="${CT_IP:-192.168.13.228/20}"
CT_GW="${CT_GW:-192.168.13.1}"
CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_CORES="${CT_CORES:-4}"
CT_MEMORY="${CT_MEMORY:-8192}"
CT_SWAP="${CT_SWAP:-512}"
CT_DISK="${CT_DISK:-16}"
CT_STORAGE="${CT_STORAGE:-friends-pool-zfs}"

TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
TEMPLATE="${TEMPLATE:-debian-13-standard_13.6-1_amd64.tar.zst}"

REPO_URL="${REPO_URL:-https://github.com/hardKOrr/homelab-infra}"
REPO_BRANCH="${REPO_BRANCH:-master}"

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

RD_HOST="${CT_IP%%/*}"
RD_PORT="${RD_PORT:-4440}"
RD_URL="http://${RD_HOST}:${RD_PORT}"

# ── Helpers ────────────────────────────────────────────────────────────────────
log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

in_ct() { pct exec "$VMID" -- "$@"; }

# ── Preflight ──────────────────────────────────────────────────────────────────
log "Preflight"
[ "$(id -u)" -eq 0 ] || die "must run as root on a Proxmox node"
command -v pct >/dev/null    || die "pct not found — run this on a Proxmox node, not inside a container"
command -v pveam >/dev/null  || die "pveam not found"
pvesm status --storage "$CT_STORAGE" >/dev/null 2>&1 || die "storage '$CT_STORAGE' not available on this node"
info "node $(hostname), target VMID $VMID at $RD_URL"

# Resolve the keys now so we fail before building anything, not after.
if [ -z "$SSH_PUBKEY" ] && [ -r "$SSH_PUBKEY_FILE" ]; then
  SSH_PUBKEY="$(grep -E '^(ssh-|ecdsa-)' "$SSH_PUBKEY_FILE" 2>/dev/null || true)"
fi
if [ -z "$SSH_PUBKEY" ]; then
  info "WARNING: no SSH public key found (looked in $SSH_PUBKEY_FILE)"
  info "         the container will only be reachable via 'pct exec $VMID -- ...'"
  info "         set SSH_PUBKEY=... or SSH_PUBKEY_FILE=... to get root SSH"
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
  info "container $VMID does not exist — creating it"
fi

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
    --unprivileged 1 --features nesting=1 --onboot 1 --ostype debian \
    --description "Rundeck — homelab-infra UI layer (bootstrap-rundeck.sh)"
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
  CT_HOSTNAME="$CT_HOSTNAME" \
  REPO_URL="$REPO_URL" \
  REPO_BRANCH="$REPO_BRANCH" \
  REPO_DIR="$REPO_DIR" \
  VENV_DIR="$VENV_DIR" \
  CRED_FILE="$CRED_FILE" \
  ANSIBLE_CORE_SPEC="$ANSIBLE_CORE_SPEC" \
  SSH_PUBKEY="$SSH_PUBKEY" \
  bash -s <<'GUEST'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LANG=C.UTF-8 LC_ALL=C.UTF-8

say() { printf '    %s\n' "$*"; }

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
apt-get install -y -qq \
  ca-certificates curl gnupg git jq rsync sudo \
  openssh-client openssh-server python3-venv python3-pip \
  unattended-upgrades apt-transport-https >/dev/null

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
say "rundeck $(dpkg-query -W -f='${Version}' rundeck)"

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

# Jobs inherit the service environment, so pin the locale for the ansible plugin too.
mkdir -p /etc/systemd/system/rundeckd.service.d
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
  RD_ADMIN_PW="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 28)"
  cp -n /etc/rundeck/realm.properties /etc/rundeck/realm.properties.orig
  sed -i "s|^admin:admin,|admin:${RD_ADMIN_PW},|" /etc/rundeck/realm.properties
  chown root:rundeck /etc/rundeck/realm.properties
  chmod 0640 /etc/rundeck/realm.properties
  touch "$CRED_FILE"; chmod 0600 "$CRED_FILE"
  printf 'RUNDECK_URL=%s\nRUNDECK_ADMIN_USER=admin\nRUNDECK_ADMIN_PASSWORD=%s\n' \
    "$RD_URL" "$RD_ADMIN_PW" > "$CRED_FILE"
else
  say "admin password already customised — leaving it alone"
fi

# -- ansible venv ---------------------------------------------------------------
# Debian 13 ships Python 3.13. This matters: community.proxmox 2.0.0 needs ansible-core
# >= 2.17, which needs a Python 3.11+ controller — the reason this cannot run on Debian 11.
say "ansible venv at $VENV_DIR"
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q --upgrade pip wheel
"$VENV_DIR/bin/pip" install -q "$ANSIBLE_CORE_SPEC" proxmoxer requests
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
chown -R rundeck:rundeck "$REPO_DIR" "$VENV_DIR" /var/lib/rundeck/.ansible
systemctl enable rundeckd >/dev/null 2>&1 || true
systemctl restart rundeckd
GUEST

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
log "API token"
RD_ADMIN_PW="$(in_ct sh -c "sed -n 's/^RUNDECK_ADMIN_PASSWORD=//p' $CRED_FILE 2>/dev/null" || true)"

if [ -z "$RD_ADMIN_PW" ]; then
  info "no stored admin password at ${CRED_FILE} in the container — skipping token issue"
  info "create one in the UI under: admin -> Profile -> User API Tokens"
else
  COOKIE="$(mktemp)"; trap 'rm -f "$COOKIE"' EXIT
  curl -s -c "$COOKIE" -o /dev/null -m 15 "$RD_URL/user/login"
  curl -s -b "$COOKIE" -c "$COOKIE" -o /dev/null -m 15 \
    -d "j_username=admin" --data-urlencode "j_password=${RD_ADMIN_PW}" \
    "$RD_URL/j_security_check"

  EXISTING="$(curl -s -b "$COOKIE" -H 'Accept: application/json' -m 15 \
    "$RD_URL/api/58/tokens/admin" 2>/dev/null | grep -c '"name":"homelab-infra"' || true)"

  if [ "${EXISTING:-0}" -gt 0 ]; then
    info "token 'homelab-infra' already exists — keeping it (secrets are shown only at creation)"
    info "to rotate: revoke it in the UI and re-run this script"
  else
    RESP="$(curl -s -b "$COOKIE" -m 20 -X POST \
      -H 'Content-Type: application/json' -H 'Accept: application/json' \
      -d '{"user":"admin","roles":["*"],"duration":"0","name":"homelab-infra"}' \
      "$RD_URL/api/58/tokens/admin")"
    RD_TOKEN="$(printf '%s' "$RESP" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')"
    if [ -n "$RD_TOKEN" ]; then
      info "issued non-expiring token 'homelab-infra'"
      in_ct sh -c "grep -q '^RUNDECK_API_TOKEN=' $CRED_FILE 2>/dev/null \
        || echo 'RUNDECK_API_TOKEN=${RD_TOKEN}' >> $CRED_FILE"
    else
      info "token request failed: $RESP"
    fi
  fi
fi

# ── Summary ────────────────────────────────────────────────────────────────────
log "Done"
cat <<EOF
    Rundeck   $RD_URL
    Container VMID $VMID ($CT_HOSTNAME) on $(hostname)
    SSH       ssh root@${RD_HOST}
    Repo      $REPO_DIR
    Ansible   $VENV_DIR/bin/ansible
    Creds     pct exec $VMID -- cat $CRED_FILE

    Copy RUNDECK_API_TOKEN from that file into this repo's .env (gitignored).

    Still to do (needs apps + wiring first):
      - create the homelab-infra Rundeck project
      - configure the git SCM import plugin against $REPO_URL
      - stage Key Storage: keys/proxmox/api-token, keys/vaultwarden/admin-token
      - author rundeck/jobs/*.yaml
EOF
