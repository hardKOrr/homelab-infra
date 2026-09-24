#!/usr/bin/env bash
# check-lab-leaks.py: whole-token matching, value-free output, and the skip path.
#
# Runs against a throwaway values file and sandbox repository; the operator's real values
# file is never read.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
check="$repo/gate/check-lab-leaks.py"
py="$HOME/.venvs/homelab-ansible/bin/python"
[ -x "$py" ] || py=python3
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fail() { echo "FAIL test-lab-leaks: $*" >&2; exit 1; }

cat > "$tmp/values.yml" <<'YAML'
current:
  lab-domain: fixture-lab.test
  pve-node: node-1
  runner-ip: 10.0.0.1
retired:
  old:
    runner-vmid: "100000001"
YAML
export HOMELAB_LAB_VALUES="$tmp/values.yml"

# Whole-token and subdomain matching on stdin; the output names the key, not the value.
set +e
out="$(printf 'see auth.fixture-lab.test\nrun on node-1 now\n' | "$py" "$check" --stdin)"
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "a real value on stdin must fail (rc=$rc)"
grep -q '<stdin>:1: <lab-domain>' <<<"$out" || fail "subdomain of lab-domain not reported"
grep -q '<stdin>:2: <pve-node>' <<<"$out" || fail "pve-node not reported"
grep -qE 'fixture-lab\.test|node-1' <<<"$out" && fail "output must never echo a value"

# Near-misses are not hits: a longer node name, a longer address, a longer VMID.
printf 'node-10 at 10.0.0.14 and 10.0.0.1.5 vmid 1000000012\n' | "$py" "$check" --stdin >/dev/null \
    || fail "near-miss tokens must pass"

# Placeholders pass.
printf 'run on <pve-node> at <runner-ip>\n' | "$py" "$check" --stdin >/dev/null \
    || fail "placeholders must pass"

# Tracked-file scan reports path and line.
git -C "$tmp" init -q sandbox
printf 'ok\nvmid 100000001\n' > "$tmp/sandbox/notes.md"
git -C "$tmp/sandbox" add notes.md
set +e
out="$(cd "$tmp/sandbox" && "$py" "$check")"
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "a tracked file with a retired value must fail"
grep -q 'notes.md:2: <runner-vmid>' <<<"$out" || fail "tracked-file finding not located"

# No values file: skipped, passing.
HOMELAB_LAB_VALUES="$tmp/absent.yml" "$py" "$check" --stdin </dev/null | grep -q skipped \
    || fail "missing values file must skip"

echo "PASS test-lab-leaks"
