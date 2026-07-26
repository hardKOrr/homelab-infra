#!/usr/bin/env bash
# redact-config.sh — print the authored config/ tree as YAML with secrets masked.
#
# Usage:
#   bash ansible/scripts/redact-config.sh <config-dir> [instance]
#
# With no instance, every authored file is printed (proxmox.yml, infrastructure.yml and
# every config/apps/*.yml). With an instance, only config/apps/<instance>.yml.
#
# Redaction walks the PARSED structure rather than regexing the text, so a secret cannot
# survive by being quoted, folded or indented unusually. A key is masked when its name
# contains token, secret, password, api_key or arl — which covers
# proxmox.api_token_secret, vaultwarden.admin_token, sso.token, dns.api_key,
# dns.api_secret, metrics.admin_password and every provider spelling of the same idea,
# without enumerating them.
#
# Comments do not survive the round trip. That is deliberate and cheap: the authoritative
# copy is the file on the runner, this is a view of it, and the Get Config archive carries
# the tree verbatim for anyone who needs the real thing.
#
# Exit codes: 0 printed something, 1 nothing matched or the tree is unreadable.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config_dir="${1:?usage: redact-config.sh <config-dir> [instance]}"
instance="${2:-}"

py_bin="$(bash "$script_dir/resolve-python.sh")" || exit 1

exec "$py_bin" - "$config_dir" "$instance" <<'PY'
import glob
import os
import sys

import yaml

CONFIG_DIR, INSTANCE = sys.argv[1], sys.argv[2]
MARKERS = ("token", "secret", "password", "api_key", "arl")
# Identifiers, not credentials. They match a marker as a substring but naming them is
# harmless, and masking them would make the output lossy: `api_token_id` is the token's
# NAME, and a redacted copy you cannot paste back into Configure App is not a view of
# your config, it is a puzzle.
NOT_SECRET = ("api_token_id", "token_id", "client_id", "notification_id", "tokenid")
MASK = "**REDACTED**"


def redact(node, key_name=""):
    if isinstance(node, dict):
        return {k: redact(v, str(k)) for k, v in node.items()}
    if isinstance(node, list):
        return [redact(v, key_name) for v in node]
    lowered = key_name.lower()
    if lowered in NOT_SECRET:
        return node
    if node not in (None, "") and any(m in lowered for m in MARKERS):
        return MASK
    return node


if INSTANCE:
    paths = [os.path.join(CONFIG_DIR, "apps", "%s.yml" % INSTANCE)]
    paths = [p for p in paths if os.path.isfile(p)]
else:
    paths = [p for p in (os.path.join(CONFIG_DIR, "proxmox.yml"),
                         os.path.join(CONFIG_DIR, "infrastructure.yml"))
             if os.path.isfile(p)]
    paths += sorted(glob.glob(os.path.join(CONFIG_DIR, "apps", "*.yml")))

if not paths:
    sys.stderr.write(
        "no config file found in %s%s\n"
        % (CONFIG_DIR, " for instance %r" % INSTANCE if INSTANCE else ""))
    sys.exit(1)

for path in paths:
    rel = os.path.relpath(path, CONFIG_DIR).replace(os.sep, "/")
    print("# ---- %s ----" % rel)
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        print("# UNPARSEABLE: %s" % exc)
        print("")
        continue
    if data is None:
        print("# (empty file — every key falls through to the defaults)")
        print("")
        continue
    print(yaml.safe_dump(redact(data), default_flow_style=False,
                         sort_keys=False, width=100).rstrip())
    print("")
PY
