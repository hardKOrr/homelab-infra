#!/bin/bash
# Regression checks for the controller-side recovery coverage section in Lab Status.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
status="$repo/ansible/playbooks/maintenance/status.yml"

grep -q -- '- "{{ ansible_playbook_python }}"' "$status"
grep -q '_st_recovery_raw.stderr_lines' "$status"
grep -q '_st_recovery_error' "$status"
grep -q 'coverage collector failed:' "$status"

echo "recovery status collector checks passed"
