#!/usr/bin/env bash
# with-proxmox-env.sh — export the community.proxmox inventory plugin's PROXMOX_API_* connection
# environment from a homelab-infra user-vars file, then exec the given ansible command.
#
# Why: community.proxmox 2.0.0's inventory plugin cannot receive -e extra vars in its connection
# options. This was verified against the plugin; inventory/proxmox.yml reads
# PROXMOX_API_* via lookup('env', ...) instead; this wrapper fills them from the same user-vars file
# -e @<file> feeds the playbook, keeping the Proxmox host/token in one place.
#
# Accepts either config file shape:
#   config/proxmox.yml        top-level `proxmox:` (the current config model)
#   user-vars.yml             `homelabinfra_config: {proxmox: ...}` (legacy back-compat)
#
# The token secret is read from the environment first, the file second. The recommended
# shape (slice 010) is a config/proxmox.yml with `api_token_secret` absent entirely and
# PROXMOX_API_TOKEN supplied by Rundeck Key Storage, so the
# platform's most privileged credential never lands in a file. These env vars override
# the file when both are present:
#
#   PROXMOX_API_TOKEN         the token secret            (file: proxmox.api_token_secret)
#   PROXMOX_API_TOKEN_ID      the token id                (file: proxmox.api_token_id)
#   PROXMOX_API_USER          the owning user             (file: proxmox.api_user)
#   PROXMOX_API_HOST/_PORT    the endpoint                (file: proxmox.api_host/api_port)
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

# "$1" is the ansible command we are about to exec, so its sibling python3 is the ansible
# venv's interpreter — the one that has PyYAML. Hand it to the shared resolver.
py_bin="$(bash "$(dirname -- "${BASH_SOURCE[0]}")/resolve-python.sh" "$1")" || exit 1

# Emit `export KEY=VALUE` lines; fail loudly if a required value is absent from BOTH the
# environment and the file.
env_exports="$("$py_bin" - "$vars_file" <<'PY'
import os, sys, yaml
with open(sys.argv[1]) as fh:
    data = yaml.safe_load(fh) or {}
# config/proxmox.yml puts `proxmox:` at the top level; the legacy user-vars file wraps
# it in `homelabinfra_config:`. Take whichever one carries the connection keys.
prox = (data.get("proxmox") or {}) or ((data.get("homelabinfra_config") or {}).get("proxmox") or {})

def pick(env_name, file_key, default=None):
    """Environment wins over the file; the file wins over the default."""
    return os.environ.get(env_name) or prox.get(file_key) or default

resolved = {
    "PROXMOX_API_HOST":         pick("PROXMOX_API_HOST", "api_host"),
    "PROXMOX_API_PORT":         pick("PROXMOX_API_PORT", "api_port", 8006),
    "PROXMOX_API_USER":         pick("PROXMOX_API_USER", "api_user", "root@pam"),
    "PROXMOX_API_TOKEN_ID":     pick("PROXMOX_API_TOKEN_ID", "api_token_id"),
    "PROXMOX_API_TOKEN_SECRET": pick("PROXMOX_API_TOKEN", "api_token_secret"),
}
missing = [k for k, v in resolved.items() if not v]
if missing:
    sys.stderr.write(
        "ERROR: Proxmox connection incomplete - missing %s\n"
        "       supply them in %s, or as environment variables\n"
        "       (the token secret's env var is PROXMOX_API_TOKEN)\n"
        % (", ".join(sorted(missing)), sys.argv[1]))
    sys.exit(1)

def q(v):  # single-quote-safe shell literal
    return "'" + str(v).replace("'", "'\"'\"'") + "'"
for key, value in resolved.items():
    print("export %s=%s" % (key, q(value)))
PY
)" || { echo "ERROR: failed to resolve the Proxmox connection from $vars_file" >&2; exit 1; }

eval "$env_exports"
exec "$@"
