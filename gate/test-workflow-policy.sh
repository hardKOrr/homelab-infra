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

# ── 7. workflow-level env inherits secrets into a pull_request job ────────────
set +e
out="$(run_case pr-workflow-env-secrets '
on: pull_request
permissions:
  contents: read
env:
  TOKEN: "${{ secrets.TOKEN }}"
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
')"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "a workflow-level env secrets reference on a pull_request workflow must fail"
grep -q "workflow-level env references the secrets context" <<<"$out" \
    || fail "workflow-level-env-secrets failure message not found"

# ── 8. self-hosted runner selected via a runs-on mapping, no environment gate ─
set +e
out="$(run_case self-hosted-mapping-no-gate '
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: {group: pve-runners, labels: self-hosted}
    steps:
      - run: echo hi
')"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "a mapping-form self-hosted runs-on without an environment gate must fail"
grep -q "self-hosted runner without an environment" <<<"$out" \
    || fail "mapping-form self-hosted failure message not found"

# ── 9. uppercase SELF-HOSTED label without an environment gate ────────────────
set +e
out="$(run_case self-hosted-uppercase-no-gate '
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: [SELF-HOSTED, pve]
    steps:
      - run: echo hi
')"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "an uppercase SELF-HOSTED label without an environment gate must fail"
grep -q "self-hosted runner without an environment" <<<"$out" \
    || fail "uppercase-label self-hosted failure message not found"

# ── 10. dynamic runs-on expression on a pull_request trigger, no environment gate
set +e
out="$(run_case dynamic-runs-on-no-gate '
on: pull_request
permissions:
  contents: read
jobs:
  build:
    runs-on: "${{ github.event.pull_request.head.ref }}"
    steps:
      - run: echo hi
')"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "a dynamic runs-on expression on a pull_request trigger must fail closed"
grep -q "self-hosted runner without an environment" <<<"$out" \
    || fail "dynamic-runs-on failure message not found"

# ── 11. actions/upload-artifact without a redaction step is rejected ──────────
set +e
out="$(run_case upload-artifact-no-redaction '
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
      - uses: actions/upload-artifact@v4
        with:
          name: logs
          path: /tmp/logs
')"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "actions/upload-artifact without a redaction step must fail"
grep -q "uses actions/upload-artifact" <<<"$out" || fail "upload-artifact failure message not found"

# ── 12. mixed-case actions/upload-artifact reference is still rejected ────────
set +e
out="$(run_case upload-artifact-mixed-case '
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
      - uses: Actions/Upload-Artifact@v4
        with:
          name: logs
          path: /tmp/logs
')"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "a mixed-case actions/upload-artifact reference must fail"
grep -q "uses actions/upload-artifact" <<<"$out" || fail "mixed-case upload-artifact failure message not found"

# ── 13. a job with no upload-artifact step is accepted ────────────────────────
run_case no-upload-artifact '
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
' >/dev/null || fail "a job with no artifact upload must pass"

echo "workflow-policy focused tests passed."
