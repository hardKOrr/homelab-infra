#!/usr/bin/env bash
# Focused tests for ansible/scripts/allocate-ip.py — the address allocator.
# Every case here is a decision slice 011 makes, including the ones that must FAIL:
# an allocator that quietly hands back a plausible address is how a lab's addressing
# scheme decays one deploy at a time.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
alloc="$repo/ansible/scripts/allocate-ip.py"

fail() { echo "allocate-ip test failed: $*" >&2; exit 1; }

# Expect success and an exact address.
expect() {
  local want="$1" request="$2" got
  got="$(printf '%s' "$request" | python3 "$alloc")" \
    || fail "request was refused but should have succeeded: $request"
  got="$(printf '%s' "$got" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ip_address"])')"
  [ "$got" = "$want" ] || fail "expected $want, got $got, for: $request"
}

# Expect refusal, and a reason mentioning the given text — the message is the
# feature here, so a refusal with an unhelpful reason is a failure.
refuse() {
  local because="$1" request="$2" err rc
  set +e
  err="$(printf '%s' "$request" | python3 "$alloc" 2>&1 >/dev/null)"
  rc=$?
  set -e
  [ "$rc" -ne 0 ] || fail "request should have been refused: $request"
  grep -qi -- "$because" <<<"$err" \
    || fail "refusal did not mention '$because' (said: $err)"
}

net='"cidr":"192.168.0.0/20","gateway":"192.168.0.1"'

# Pool-less: the flat walk still works, so an existing config keeps its behaviour.
expect 192.168.0.10 "{$net,\"offset\":10,\"in_use\":[]}"
expect 192.168.0.12 "{$net,\"offset\":10,\"in_use\":[\"192.168.0.10\",\"192.168.0.11\"]}"

# The gateway is never allocated, even when nothing lists it as in use — the flat
# allocator's blind spot, and this lab's gateway sits inside the span it walks.
expect 192.168.0.2 "{$net,\"offset\":1,\"in_use\":[]}"

# Reserved spans hold whether or not Proxmox knows about the device.
expect 192.168.0.40 \
  "{$net,\"offset\":10,\"reserved\":[\"192.168.0.10-192.168.0.39\"],\"in_use\":[]}"
expect 192.168.0.16 "{$net,\"offset\":10,\"reserved\":[\"192.168.0.8/29\"],\"in_use\":[]}"

# A pool confines allocation to its own span, whatever the flat walk would have done.
pool='"pool":{"name":"media","range":"192.168.2.0-192.168.2.63"}'
expect 192.168.2.0 "{$net,$pool,\"offset\":10,\"in_use\":[\"192.168.0.10\"]}"
expect 192.168.2.2 "{$net,$pool,\"in_use\":[\"192.168.2.0\",\"192.168.2.1\"]}"

# An exhausted pool is refused by name. It never spills into the wider subnet:
# spilling is exactly how a guest ends up in the wrong VLAN once the estate is cut up.
refuse "pool 'media'" \
  "{$net,\"pool\":{\"name\":\"media\",\"range\":\"192.168.2.0-192.168.2.1\"},\"in_use\":[\"192.168.2.0\",\"192.168.2.1\"]}"
refuse "not inside network" \
  "{$net,\"pool\":{\"name\":\"elsewhere\",\"range\":\"10.9.0.0-10.9.0.31\"}}"
refuse "neither a range nor a cidr" "{$net,\"pool\":{\"name\":\"empty\"}}"

# A pin is honoured exactly, or refused with the conflict named.
expect 192.168.0.50 "{$net,\"pin\":\"192.168.0.50\",\"offset\":10,\"in_use\":[]}"
refuse "already held" "{$net,\"pin\":\"192.168.0.50\",\"in_use\":[\"192.168.0.50\"]}"
refuse "reserved" "{$net,\"pin\":\"192.168.0.1\"}"
refuse "outside" "{$net,\"pin\":\"192.168.9.9\",$pool}"
refuse "outside" "{$net,\"pin\":\"10.0.0.5\"}"
refuse "not an address" "{$net,\"pin\":\"not-an-ip\"}"

# max_hosts caps the flat walk, and an exhausted network says so.
refuse "either held by a guest or reserved" \
  "{$net,\"offset\":10,\"max_hosts\":2,\"in_use\":[\"192.168.0.10\",\"192.168.0.11\"]}"

# Malformed input is refused, never guessed at.
refuse "not a subnet" '{"cidr":"192.168.0.0/33"}'
refuse "no cidr" '{}'
refuse "ends before it begins" \
  "{$net,\"pool\":{\"name\":\"backwards\",\"range\":\"192.168.0.60-192.168.0.40\"}}"

echo "allocate-ip focused tests passed."
