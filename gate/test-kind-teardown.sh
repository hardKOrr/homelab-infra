#!/usr/bin/env bash
# Focused tests for gate/lib-kind-cleanup.sh's EXIT trap: gate/kind.sh's teardown must
# fail the run whenever either cleanup step (namespace removal, cluster delete) fails,
# and must never let a clean cleanup mask an earlier converge/verify failure. Neither
# case runs a real Kind cluster or Ansible — `kind`, `ansible-playbook`, and `sudo` are
# stubbed per case.
set -uo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
lib="$repo/gate/lib-kind-cleanup.sh"

fail() { echo "kind-teardown test failed: $*" >&2; exit 1; }

# Runs a tiny script that sources the library, stubs `kind` and `ansible-playbook` per
# the case, installs the trap, then exits $step_rc — the status a real run would already
# be carrying by the time its last command (converge/verify) finished. Echoes the
# script's actual exit code.
run_case() {
    local step_rc="$1" cluster_present="$2" teardown_rc="$3" delete_rc="$4"
    bash -c '
        set -uo pipefail
        . "$1"
        cluster_present="$3"
        teardown_rc="$4"
        delete_rc="$5"
        kind() {
            if [ "$1" = "get" ]; then
                [ "$cluster_present" = "yes" ] && echo "test-cluster"
                return 0
            fi
            [ "$1" = "delete" ] && return "$delete_rc" || return 0
        }
        ansible-playbook() { return "$teardown_rc"; }
        install_kind_cleanup_trap test-cluster /dev/null
        exit "$2"
    ' _ "$lib" "$step_rc" "$cluster_present" "$teardown_rc" "$delete_rc" >/dev/null 2>&1
    echo "$?"
}

got="$(run_case 0 yes 0 0)"
[ "$got" -eq 0 ] || fail "clean run + clean namespace teardown + clean cluster delete should exit 0, got $got"

got="$(run_case 0 no 0 0)"
[ "$got" -eq 0 ] || fail "clean run with the cluster already gone should still exit 0, got $got"

got="$(run_case 0 yes 1 0)"
[ "$got" -ne 0 ] || fail "clean run + FAILED namespace teardown must not exit 0, got $got"

got="$(run_case 0 yes 0 1)"
[ "$got" -ne 0 ] || fail "clean run + FAILED cluster delete must not exit 0, got $got"

got="$(run_case 3 yes 0 0)"
[ "$got" -eq 3 ] || fail "a run that already failed (rc=3) must keep that code through clean cleanup, got $got"

got="$(run_case 5 yes 1 1)"
[ "$got" -eq 5 ] || fail "a run that already failed (rc=5) must keep that code even when cleanup also fails, got $got"

echo "kind teardown trap tests passed."
