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
#   the live lab's own shape, zero-padding in both variable octets, a 10/8 lab where the
#   second octet is 0 and the FIRST becomes the prefix, and both ends of an octet's range.
cases=(
  192.168.13.228
  192.168.0.3
  192.168.2.20
  192.168.0.200
  10.0.4.7
  10.0.0.1
  172.16.255.254
  192.168.100.100
)

jinja_out="$(python3 - "$repo" "${cases[@]}" <<'PY'
import re
import sys
from pathlib import Path

from jinja2.nativetypes import NativeEnvironment

repo, addresses = Path(sys.argv[1]), sys.argv[2:]
task = (repo / "ansible" / "tasks" / "proxmox" / "ip-to-vmid-guest.yml").read_text(encoding="utf-8")

# Lift the three vars the derivation is built from, exactly as the task file spells them.
def expr(name):
    m = re.search(r'^\s*%s:\s*"(.+)"\s*$' % re.escape(name), task, re.M)
    if not m:
        raise SystemExit(
            "vmid-from-ip test: %s is no longer a one-line var in ip-to-vmid-guest.yml. "
            "The derivation moved; update this test to read it where it now lives." % name
        )
    return m.group(1)

env = NativeEnvironment()
octets_e, prefix_e, vmid_e = expr("octets"), expr("prefix_octet"), expr("vmid_from_ip")

for address in addresses:
    ctx = {"guest_ip": address}
    ctx["octets"] = env.from_string(octets_e).render(**ctx)
    ctx["prefix_octet"] = env.from_string(prefix_e).render(**ctx)
    print("%s %s" % (address, env.from_string(vmid_e).render(**ctx)))
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

# The runner must not be exempt from the rule: no prompt may reintroduce a hand-typed VMID.
if grep -qE '^\s*ask VMID\b' "$repo/rundeck/bootstrap-rundeck.sh"; then
  echo "FAIL: bootstrap-rundeck.sh asks for a VMID again. It is derived from CT_IP; see the VMID tunable." >&2
  rc=1
fi

if [ "$rc" -eq 0 ]; then
  echo "vmid-from-ip: OK (${#cases[@]} addresses, both implementations agree)"
fi
exit "$rc"
