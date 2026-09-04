#!/bin/bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
workload="$repo/ansible/roles/maintainerr/templates/manifest.yaml.j2"
restore="$repo/ansible/roles/maintainerr/templates/restore-job.yaml.j2"
workload_claim=$(sed -n "s/^  name: {{ instance }}-\\(.*\\)$/\\1/p" "$workload" | head -n 1)
restore_claim=$(sed -n "s/^            claimName: {{ _js_r_target }}-\\(.*\\)$/\\1/p" "$restore")
if [ "$workload_claim" != "data" ] || [ "$restore_claim" != "$workload_claim" ]; then
  echo "ERROR: Maintainerr restore PVC ($restore_claim) does not match workload PVC ($workload_claim)." >&2
  exit 1
fi
echo "Maintainerr restore PVC contract passed."
