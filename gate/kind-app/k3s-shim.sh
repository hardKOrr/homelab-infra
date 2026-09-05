#!/bin/bash
# Stand-in for the real `k3s` binary every ansible/tasks/kubernetes/*.yml task shells out
# to (`argv: [k3s, kubectl, ...]`). The real binary bundles kubectl inside a k3s
# distribution; Kind's upstream kubectl has no such wrapper, so this script restores the
# one subcommand those tasks actually call and points it at the Kind cluster's own
# kubeconfig — installed by gate/kind.sh, never at a real cluster.
#
# The kubeconfig path is substituted in by gate/kind.sh at install time, not read from an
# environment variable: every caller here runs `become: true` (a real k3s node needs
# root), and sudo drops the caller's environment by default, which would silently point
# this shim at no kubeconfig at all.
set -euo pipefail

KUBECONFIG="__KIND_APP_KUBECONFIG__"
export KUBECONFIG

if [ "${1:-}" != "kubectl" ]; then
    echo "k3s-shim: only the 'kubectl' subcommand is faked (got: ${1:-<none>})" >&2
    exit 1
fi
shift

exec kubectl "$@"
