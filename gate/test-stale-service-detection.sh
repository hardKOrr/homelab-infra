#!/usr/bin/env bash
# Focused regression checks for slice 507's static contracts. The code executes on a
# guest, so the gate verifies the generated scripts preserve the safety boundaries.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
updates="$repo/ansible/tasks/bootstrap/configure-unattended-upgrades.yml"
timer="$repo/ansible/tasks/maintenance/install-guest-timer.yml"
status="$repo/ansible/playbooks/maintenance/status.yml"
docker_host="$repo/ansible/playbooks/docker/create-docker-host.yml"

fail() { echo "stale-service-detection test failed: $*" >&2; exit 1; }
expect() { rg -q -- "$2" "$1" || fail "missing $2 in ${1#$repo/}"; }

expect "$updates" 'name: needrestart'
expect "$updates" '\$nrconf\{restart\} = '\''l'\'';'
expect "$updates" '-b -r l'
expect "$updates" 'NEEDRESTART-SVC:'
expect "$updates" 'Stale services \(running replaced code\):'
expect "$timer" 'if \[ ! -f /var/run/reboot-required \]; then'
expect "$timer" 'systemctl restart -- "\$service"'
expect "$timer" 'docker/containerd itself'
expect "$status" '_st_stale_services'
expect "$status" 'stale services \{\{ hostvars\[h\]._st_stale_services'
expect "$docker_host" 'configure-unattended-upgrades.yml'

echo "stale-service-detection: all cases passed."
