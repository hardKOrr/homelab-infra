#!/usr/bin/env bash
# with-proxmox-env.sh — export the community.proxmox inventory plugin's PROXMOX_API_* connection
# environment from a homelab-infra user-vars file, then exec the given ansible command.
#
# Why: community.proxmox 2.0.0's inventory plugin cannot receive -e extra vars in its connection
# options (see .claude/plans/done/fix-inventory-url-and-extra-vars.md). inventory/proxmox.yml reads
# PROXMOX_API_* via lookup('env', ...) instead; this wrapper fills them from the same user-vars file
# -e @<file> feeds the playbook, keeping the Proxmox host/token in one place.
#
# Accepts either config file shape:
#   config/proxmox.yml        top-level `proxmox:` (the current config model)
#   user-vars.yml             `homelabinfra_config: {proxmox: ...}` (legacy back-compat)
#
# Usage:  with-proxmox-env.sh <proxmox-config.yml> <ansible-command> [args...]
# Example (from ansible/), and the form every Rundeck job step uses:
#   bash scripts/with-proxmox-env.sh ../config/proxmox.yml \
#     ansible-playbook -i inventory/ playbooks/bootstrap.yml
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <user-vars.yml> <ansible-command> [args...]" >&2
  exit 2
fi

vars_file="$1"; shift
[ -f "$vars_file" ] || { echo "ERROR: proxmox config file not found: $vars_file" >&2; exit 1; }

# Parsing the config needs PyYAML, which a distro's bare python3 usually lacks — the
# interpreter that has it is the one in the ansible venv. "$1" is the ansible command we
# are about to exec, so its sibling python3 is that venv's interpreter. Prefer an explicit
# $PYTHON, then that sibling, then whatever python3 is on PATH; take the first that
# actually imports yaml so a missing module fails here with a clear message.
py_bin=""
for _cand in ${PYTHON:-} "$(dirname -- "$1")/python3" python3; do
  [ -n "$_cand" ] || continue
  command -v "$_cand" >/dev/null 2>&1 || continue
  "$_cand" -c 'import yaml' >/dev/null 2>&1 || continue
  py_bin="$_cand"; break
done
[ -n "$py_bin" ] || {
  echo "ERROR: no python3 with PyYAML found (tried \$PYTHON, $(dirname -- "$1")/python3, python3)" >&2
  echo "       install PyYAML into the ansible venv, or set PYTHON=/path/to/python3" >&2
  exit 1
}

# Emit `export KEY=VALUE` lines; fail loudly if the proxmox block or a required key is absent.
env_exports="$("$py_bin" - "$vars_file" <<'PY'
import sys, yaml
with open(sys.argv[1]) as fh:
    data = yaml.safe_load(fh) or {}
# config/proxmox.yml puts `proxmox:` at the top level; the legacy user-vars file wraps
# it in `homelabinfra_config:`. Take whichever one carries the connection keys.
prox = (data.get("proxmox") or {}) or ((data.get("homelabinfra_config") or {}).get("proxmox") or {})
missing = [k for k in ("api_host", "api_token_id", "api_token_secret") if not prox.get(k)]
if missing:
    sys.stderr.write("ERROR: %s missing proxmox key(s): %s\n" % (sys.argv[1], ", ".join(missing)))
    sys.exit(1)
def q(v):  # single-quote-safe shell literal
    return "'" + str(v).replace("'", "'\"'\"'") + "'"
print("export PROXMOX_API_HOST=%s" % q(prox["api_host"]))
print("export PROXMOX_API_PORT=%s" % q(prox.get("api_port") or 8006))
print("export PROXMOX_API_USER=%s" % q(prox.get("api_user") or "root@pam"))
print("export PROXMOX_API_TOKEN_ID=%s" % q(prox["api_token_id"]))
print("export PROXMOX_API_TOKEN_SECRET=%s" % q(prox["api_token_secret"]))
PY
)" || { echo "ERROR: failed to parse Proxmox connection from $vars_file" >&2; exit 1; }

eval "$env_exports"
exec "$@"
