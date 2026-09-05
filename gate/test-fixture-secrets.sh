#!/usr/bin/env bash
# Focused regression tests for gate/check-fixture-secrets.py — issue #34's fixture/artifact
# secret-shape boundary.
#
#   1. Negative: the real tracked fixtures under gate/fixtures/ and ansible/molecule/ pass
#      clean today. This is the property the checker exists to preserve.
#   2. Positive: a secret-shaped key with a value that does not look like a reviewable
#      placeholder is caught, by key path and file.
#   3. Positive: a tracked path under config/ is caught.
#   4. Negative: a secret-shaped key with an obvious placeholder value (the same convention
#      the real fixtures already use) is accepted — the checker must not force every
#      documented credential field out of a fixture, only a value that could pass for real.
#   5. The failure output for case 2 names the offending key path but never echoes the
#      value itself — issue #34's fourth acceptance criterion (test failure output must
#      not print a secret-shaped value).
#
# Everything here runs against a throwaway git worktree so "tracked" reflects the fixture
# under test, not this repository's own history.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
py_bin="$(bash "$repo/ansible/scripts/resolve-python.sh")"
checker="$repo/gate/check-fixture-secrets.py"

fail() { echo "fixture-secrets test failed: $*" >&2; exit 1; }

# ── 1. The real repository passes clean ───────────────────────────────────────
if ! "$py_bin" "$checker" >/tmp/fixture-secrets-real.$$ 2>&1; then
    cat /tmp/fixture-secrets-real.$$ >&2
    rm -f /tmp/fixture-secrets-real.$$
    fail "the real tracked fixtures must pass the checker"
fi
rm -f /tmp/fixture-secrets-real.$$

# ── Sandbox: a throwaway git repo standing in for "the tracked tree" ─────────
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/homelab-fixture-secrets.XXXXXX")"
trap 'rm -rf -- "$sandbox"' EXIT
cd "$sandbox"
git init -q
git config user.email test@example.com
git config user.name test

run_checker() {
    (cd "$sandbox" && "$py_bin" "$checker")
}

# ── 2. A real-looking secret value is caught ──────────────────────────────────
mkdir -p gate/fixtures/config/valid
cat > gate/fixtures/config/valid/proxmox.yml <<'YAML'
proxmox:
  api_token_secret: "8f3a1c9e7b2d4560f9a13c7e2b8d4f61"
YAML
git add -A
set +e
out="$(run_checker 2>&1)"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "a real-looking secret value must fail the checker"
grep -q "proxmox.api_token_secret" <<<"$out" || fail "failure did not name the offending key path"
grep -q "8f3a1c9e7b2d4560f9a13c7e2b8d4f61" <<<"$out" \
    && fail "failure output must name the offending key path, never echo the value itself"
rm -rf gate
git add -A

# ── 3. A tracked config/ path is caught ───────────────────────────────────────
mkdir -p config
echo "proxmox: {}" > config/proxmox.yml
git add -A
set +e
out="$(run_checker 2>&1)"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "a tracked config/ path must fail the checker"
grep -q "config/proxmox.yml" <<<"$out" || fail "failure did not name the tracked config/ path"
rm -rf config
git add -A

# ── 4. A placeholder-shaped secret value is accepted ──────────────────────────
mkdir -p gate/fixtures/config/valid
cat > gate/fixtures/config/valid/proxmox.yml <<'YAML'
proxmox:
  api_token_secret: "fixture-not-a-real-secret"
YAML
git add -A
run_checker >/dev/null || fail "an obvious placeholder value must be accepted"

echo "fixture-secrets focused tests passed."
