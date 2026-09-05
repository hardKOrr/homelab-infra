# Shared teardown-trap logic for gate/container.sh, split out so gate/test-container-
# teardown.sh can exercise it directly against a stubbed `molecule`. Sourced, never
# executed.
#
# install_container_cleanup_trap <scenario> registers an EXIT trap that always
# attempts `molecule destroy -s <scenario>` and preserves whatever exit status the
# script was already carrying — an earlier converge/verify failure — UNLESS destroy
# itself fails, in which case it escalates to a nonzero status. A destroy failure
# must never be swallowed: teardown is an explicit acceptance criterion of #31, and
# `|| true` here would let CI report a passing container gate while the privileged
# target (and whatever it started inside itself) stays on the runner's Docker host.
#
# _container_cleanup_scenario is a plain global, not a local closed over by the trap:
# a `local` in the installing function's frame goes out of scope the moment that
# function returns, which is before the trap ever fires.
_container_cleanup() {
    local rc=$?
    if ! molecule destroy -s "$_container_cleanup_scenario"; then
        echo "gate/container.sh: molecule destroy failed; the platform container may still be present." >&2
        [ "$rc" -eq 0 ] && rc=1
    fi
    exit "$rc"
}

install_container_cleanup_trap() {
    _container_cleanup_scenario="$1"
    trap _container_cleanup EXIT
}
