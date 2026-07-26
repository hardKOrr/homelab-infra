#!/usr/bin/env bash
# resolve-python.sh — find a python3 that can `import yaml`, and echo its path.
#
# Sourced (or run) by every script here that has to read a config/*.yml file before
# Ansible starts. A distro's bare python3 usually lacks PyYAML; the interpreter that
# has it is the one in the ansible venv. Callers pass any hint they have — usually the
# ansible command they are about to exec, whose sibling python3 IS that venv's
# interpreter — and this takes the first candidate that actually imports yaml.
#
# Usage:
#   py_bin="$(bash "$(dirname "$0")/resolve-python.sh" [hint-command])" || exit 1
#
# Candidate order: $PYTHON, the sibling python3 of the hint, $LAB_VENV/bin/python3,
# then PATH. Exits 1 with a diagnostic when none of them can parse YAML.

set -euo pipefail

hint="${1:-}"

candidates=()
[ -n "${PYTHON:-}" ] && candidates+=("$PYTHON")
[ -n "$hint" ] && candidates+=("$(dirname -- "$hint")/python3")
[ -n "${LAB_VENV:-}" ] && candidates+=("${LAB_VENV%/}/bin/python3")
candidates+=(python3)

for cand in "${candidates[@]}"; do
  [ -n "$cand" ] || continue
  command -v "$cand" >/dev/null 2>&1 || continue
  "$cand" -c 'import yaml' >/dev/null 2>&1 || continue
  printf '%s\n' "$cand"
  exit 0
done

{
  echo "ERROR: no python3 with PyYAML found (tried: ${candidates[*]})"
  echo "       install PyYAML into the ansible venv, or set PYTHON=/path/to/python3"
} >&2
exit 1
