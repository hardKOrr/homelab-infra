# Shared teardown-trap logic for gate/kind.sh, mirroring gate/lib-container-cleanup.sh's
# contract for the container harness: teardown always runs, including after a failed
# converge, and a teardown failure escalates rather than getting swallowed by `|| true` —
# recovery is an explicit acceptance criterion of #33, same as it was for #31.
#
# install_kind_cleanup_trap <cluster-name> <teardown-playbook> [k3s-shim-path] registers
# an EXIT trap that removes the instance's own namespace-scoped resources via the
# platform's real removal contract (teardown.yml) when the cluster is still reachable,
# then always deletes the whole Kind cluster, then removes the k3s shim gate/kind.sh
# installed (gate/kind.sh already refuses to run if a real k3s was present at that path,
# so this delete can never touch anything but the shim it created) — and preserves
# whatever exit status the script was already carrying unless cleanup itself fails.
_kind_cleanup() {
    local rc=$?

    if kind get clusters 2>/dev/null | grep -qx "$_kind_cleanup_cluster"; then
        ansible-playbook -i "localhost," "$_kind_cleanup_teardown_playbook" || {
            echo "gate/kind.sh: namespace teardown playbook failed; deleting the whole Kind cluster anyway." >&2
            [ "$rc" -eq 0 ] && rc=1
        }
    fi

    if ! kind delete cluster --name "$_kind_cleanup_cluster"; then
        echo "gate/kind.sh: kind delete cluster failed; the cluster may still be present on this runner." >&2
        [ "$rc" -eq 0 ] && rc=1
    fi

    if [ -n "$_kind_cleanup_k3s_shim" ] && [ -e "$_kind_cleanup_k3s_shim" ]; then
        if ! sudo rm -f "$_kind_cleanup_k3s_shim"; then
            echo "gate/kind.sh: removing the k3s shim at $_kind_cleanup_k3s_shim failed; it may still be present on this runner." >&2
            [ "$rc" -eq 0 ] && rc=1
        fi
    fi

    exit "$rc"
}

install_kind_cleanup_trap() {
    _kind_cleanup_cluster="$1"
    _kind_cleanup_teardown_playbook="$2"
    _kind_cleanup_k3s_shim="${3:-}"
    trap _kind_cleanup EXIT
}
