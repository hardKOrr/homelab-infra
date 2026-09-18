#!/usr/bin/env bash
# Regression checks for the estate-resolution wiring gate (issue #266).
set -euo pipefail

repo="$(cd -- "$(dirname -- "$BASH_SOURCE")/.." && pwd -P)"
checker="$repo/gate/check-estate-resolution.py"
tmp="$(mktemp -d /tmp/homelab-estate-resolution.XXXXXX)"
trap 'rm -rf -- "$tmp"' EXIT

mkdir -p "$tmp/apps"
cp "$repo/ansible/playbooks/apps/odoo.yml" "$tmp/apps/odoo.yml"

if ! GATE_APP_PLAYBOOKS_DIR="$tmp/apps" python3 "$checker" >"$tmp/output" 2>&1; then
    echo "FAIL: estate-resolution gate rejected Odoo with its resolve-estate include" >&2
    cat "$tmp/output" >&2
    exit 1
fi

sed -i '/include_tasks: \.\.\/\.\.\/tasks\/resolve-estate\.yml/d' "$tmp/apps/odoo.yml"

if GATE_APP_PLAYBOOKS_DIR="$tmp/apps" python3 "$checker" >"$tmp/output" 2>&1; then
    echo "FAIL: estate-resolution gate accepted Odoo after its resolve-estate include was removed" >&2
    exit 1
fi
grep -Fq 'odoo.yml' "$tmp/output" || {
    echo "FAIL: negative estate-resolution result did not name Odoo" >&2
    cat "$tmp/output" >&2
    exit 1
}

echo "PASS: estate-resolution gate rejects an unwired estate playbook and names it"
