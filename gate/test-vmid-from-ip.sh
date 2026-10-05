#!/usr/bin/env bash
# The VMID rule has two implementations. This proves they are one rule.
#
#   ansible/tasks/proxmox/ip-to-vmid-guest.yml  — Jinja, for every guest the platform creates
#   rundeck/bootstrap-rundeck.sh                — Bash, for the runner, which is built before
#                                                 Ansible exists on the machine
#
# The runner cannot use the Ansible seam: bootstrap-rundeck.sh runs on a bare Proxmox node
# and builds the container that will later hold the venv. So the arithmetic is written
# twice, and two implementations of one rule drift. The failure that drift produces is not
# loud — it is a control plane whose id says nothing about where it lives, discovered months
# later by an operator who trusted the rule.
#
# The Jinja half is evaluated out of the task file itself rather than restated here, so a
# change to that expression is what this test compares against.
#
# Nothing is contacted: pure Python and Bash against the repository.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Addresses chosen for what each one exercises, not for coverage arithmetic:
#   a /20 runner address, zero-padding in both variable octets, a 10/8 lab where the
#   second octet is 0 and the FIRST becomes the prefix, and both ends of an octet's range.
cases=(
  10.20.4.10
  192.168.0.3
  192.168.2.20
  192.168.0.200
  10.0.4.7
  10.0.0.1
  172.16.255.254
  192.168.100.100
)

export ANSIBLE_CONFIG="$repo/ansible/ansible.cfg"
jinja_out="$("$HOME/.venvs/homelab-ansible/bin/python" - "$repo" "${cases[@]}" <<'PY'
import yaml
import sys
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

repo, addresses = Path(sys.argv[1]), sys.argv[2:]
tasks = yaml.safe_load((repo / "ansible/tasks/proxmox/ip-to-vmid-guest.yml").read_text(encoding="utf-8"))
task = next(t for t in tasks if t.get("name") == "Set VMID from IP for {{ guest_type }}")

def render(expression, variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)
octets_e, prefix_e, vmid_e = (task["vars"][name] for name in ("octets", "prefix_octet", "vmid_from_ip"))

for address in addresses:
    ctx = {"guest_ip": address}
    ctx["octets"] = render(octets_e, ctx)
    ctx["prefix_octet"] = render(prefix_e, ctx)
    print("%s %s" % (address, render(vmid_e, ctx)))
PY
)"

# The Bash half, sourced out of the bootstrap script so the tested code is the shipped code.
# The script is not runnable here (it is `set -euo pipefail` on a Proxmox node from its first
# command), so the one function is extracted and defined on its own.
fn="$(awk '/^vmid_from_ip\(\) \{/,/^\}/' "$repo/rundeck/bootstrap-rundeck.sh")"
[ -n "$fn" ] || { echo "vmid-from-ip test: vmid_from_ip() not found in bootstrap-rundeck.sh" >&2; exit 1; }
die() { printf 'vmid_from_ip: %s\n' "$*" >&2; exit 1; }
eval "$fn"

rc=0
while read -r address want; do
  got="$(vmid_from_ip "$address")"
  if [ "$got" != "$want" ]; then
    printf 'FAIL %s: ip-to-vmid-guest.yml derives %s, bootstrap-rundeck.sh derives %s\n' \
      "$address" "$want" "$got" >&2
    rc=1
  fi
done <<<"$jinja_out"

# A VMID Proxmox will not accept is worse than a mismatch, because both halves agree on it.
while read -r address want; do
  case "$want" in
    ""|*[!0-9]*) printf 'FAIL %s: derived VMID %q is not a number\n' "$address" "$want" >&2; rc=1; continue ;;
  esac
  if [ "$want" -lt 100 ] || [ "$want" -gt 999999999 ]; then
    printf 'FAIL %s: derived VMID %s is outside the Proxmox range 100-999999999\n' "$address" "$want" >&2
    rc=1
  fi
done <<<"$jinja_out"

if [ "$rc" -eq 0 ]; then
  echo "vmid-from-ip: OK (${#cases[@]} addresses, both implementations agree)"
fi
exit "$rc"
