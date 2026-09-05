#!/usr/bin/env bash
# Focused regression tests for gate/check-workflow-policy.py — issue #34's hosted-lane
# workflow boundary. GATE_WORKFLOWS_DIR points the checker at a throwaway fixture
# directory per case so a fixture never needs to sit in the tracked .github/workflows/.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
py_bin="$(bash "$repo/ansible/scripts/resolve-python.sh")"
checker="$repo/gate/check-workflow-policy.py"

fail() { echo "workflow-policy test failed: $*" >&2; exit 1; }

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/homelab-workflow-policy.XXXXXX")"
trap 'rm -rf -- "$sandbox"' EXIT

# ── 1. The real repository workflow passes clean ──────────────────────────────
if ! "$py_bin" "$checker" >/tmp/workflow-policy-real.$$ 2>&1; then
    cat /tmp/workflow-policy-real.$$ >&2
    rm -f /tmp/workflow-policy-real.$$
    fail "the real .github/workflows/ must pass the checker"
fi
rm -f /tmp/workflow-policy-real.$$

run_case() {
    local name="$1" workflow="$2"
    local dir="$sandbox/$name"
    mkdir -p "$dir"
    printf '%s\n' "$workflow" > "$dir/gate.yml"
    GATE_WORKFLOWS_DIR="$dir" "$py_bin" "$checker" 2>&1
}

# ── 2. No permissions declared anywhere ───────────────────────────────────────
set +e
out="$(run_case no-permissions '
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
')"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "missing permissions must fail"
grep -q "no explicit permissions" <<<"$out" || fail "missing-permissions failure message not found"

# ── 3. secrets referenced in a pull_request-triggered job ─────────────────────
set +e
out="$(run_case pr-secrets '
on: pull_request
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: "curl -H authorization:${{ secrets.TOKEN }} https://example.invalid"
')"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "a secrets reference in a pull_request job must fail"
grep -q "references the secrets context" <<<"$out" || fail "secrets-reference failure message not found"

# ── 4. self-hosted runner without an environment gate ─────────────────────────
set +e
out="$(run_case self-hosted-no-gate '
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: [self-hosted, pve]
    steps:
      - run: echo hi
')"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "a self-hosted runner without an environment gate must fail"
grep -q "self-hosted runner without an environment" <<<"$out" || fail "self-hosted failure message not found"

# ── 5. self-hosted runner WITH an environment gate is accepted ────────────────
run_case self-hosted-with-gate '
on: workflow_dispatch
permissions:
  contents: read
jobs:
  build:
    runs-on: [self-hosted, pve]
    environment: pve-acceptance
    steps:
      - run: echo hi
' >/dev/null || fail "a self-hosted runner with an environment gate must pass"

# ── 6. secrets referenced outside a pull_request trigger is accepted ──────────
run_case push-secrets '
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ secrets.TOKEN }}"
' >/dev/null || fail "a secrets reference outside pull_request must pass"

echo "workflow-policy focused tests passed."
