#!/usr/bin/env bash
# Focused tests for gate/lib-container-cleanup.sh's EXIT trap: gate/container.sh's
# teardown must fail the run whenever `molecule destroy` itself fails, and must never
# let a clean destroy mask an earlier converge/verify failure. Neither case runs a
# real Molecule scenario — `molecule` is stubbed per case.
set -uo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
lib="$repo/gate/lib-container-cleanup.sh"

fail() { echo "container-teardown test failed: $*" >&2; exit 1; }

# Runs a tiny script that sources the library, stubs `molecule destroy` to exit
# $destroy_rc, installs the trap, then exits $step_rc — the status a real run would
# already be carrying by the time its last command (create/converge/idempotence/
# verify) finished. Echoes the script's actual exit code.
run_case() {
    local step_rc="$1" destroy_rc="$2"
    bash -c '
        set -uo pipefail
        . "$1"
        destroy_rc="$3"
        molecule() { [ "$1" = destroy ] && return "$destroy_rc" || return 0; }
        install_container_cleanup_trap docker-app
        exit "$2"
    ' _ "$lib" "$step_rc" "$destroy_rc" >/dev/null 2>&1
    echo "$?"
}

got="$(run_case 0 0)"
[ "$got" -eq 0 ] || fail "clean run + clean destroy should exit 0, got $got"

got="$(run_case 0 1)"
[ "$got" -ne 0 ] || fail "clean run + FAILED destroy must not exit 0, got $got"

got="$(run_case 3 0)"
[ "$got" -eq 3 ] || fail "a run that already failed (rc=3) must keep that code through a clean destroy, got $got"

got="$(run_case 5 1)"
[ "$got" -eq 5 ] || fail "a run that already failed (rc=5) must keep that code even when destroy also fails, got $got"

echo "container teardown trap tests passed."
