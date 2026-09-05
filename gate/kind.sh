#!/bin/bash
# Kubernetes smoke-test lane. Creates a disposable Kind cluster, converges FlareSolverr
# onto it through the real ansible/roles/flaresolverr role and the shared
# tasks/kubernetes/*.yml contract, verifies a real HTTP readiness probe, exercises the
# platform's own namespace-scoped removal path, then always deletes the cluster. See
# gate/kind-app/README.md for scope and rationale, and
# https://github.com/hardKOrr/homelab-infra/issues/33 for the tracker issue.
#
# Usage (from the repo root):
#   bash gate/kind.sh
#
# Requires a local Docker daemon (already present on GitHub-hosted Ubuntu runners; see
# .github/workflows/gate.yml's "kind" job). Installs kind and kubectl into
# $KIND_BIN_DIR if not already on PATH; installs nothing into the runner's global Docker
# or Kubernetes configuration beyond that.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"

CLUSTER_NAME="homelab-infra-kind-smoke"
KIND_BIN_DIR="${KIND_BIN_DIR:-$HOME/.local/bin}"
KIND_VERSION="v0.26.0"
KUBECTL_VERSION="v1.31.4"
KUBECONFIG_PATH="$(mktemp -d)/kubeconfig"

mkdir -p "$KIND_BIN_DIR"
export PATH="$KIND_BIN_DIR:$PATH"

if ! command -v kind >/dev/null 2>&1; then
    echo "Installing kind $KIND_VERSION into $KIND_BIN_DIR"
    curl -fsSL -o "$KIND_BIN_DIR/kind" \
        "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64"
    chmod +x "$KIND_BIN_DIR/kind"
fi

if ! command -v kubectl >/dev/null 2>&1; then
    echo "Installing kubectl $KUBECTL_VERSION into $KIND_BIN_DIR"
    curl -fsSL -o "$KIND_BIN_DIR/kubectl" \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
    chmod +x "$KIND_BIN_DIR/kubectl"
fi

ansible-galaxy collection install ansible.posix >/dev/null

# Installed to /usr/local/bin, not $KIND_BIN_DIR: every tasks/kubernetes/*.yml caller
# runs with `become: true`, and sudo's secure_path on a stock Ubuntu runner does not
# include a per-user PATH directory.
sed "s#__KIND_APP_KUBECONFIG__#${KUBECONFIG_PATH}#" gate/kind-app/k3s-shim.sh \
    | sudo tee /usr/local/bin/k3s >/dev/null
sudo chmod +x /usr/local/bin/k3s

# Teardown always runs, including after a failed converge: the trap removes the
# instance's namespace through the platform's own removal contract when the cluster is
# still reachable, then always deletes the whole cluster. See "Recovery needs" in
# tracker issue #33 and gate/lib-kind-cleanup.sh for why the trap must not swallow a
# cleanup failure with `|| true`.
# shellcheck source=lib-kind-cleanup.sh
. "$repo/gate/lib-kind-cleanup.sh"
install_kind_cleanup_trap "$CLUSTER_NAME" "$repo/gate/kind-app/teardown.yml"

kind create cluster --name "$CLUSTER_NAME" --config gate/kind-app/kind-config.yaml \
    --kubeconfig "$KUBECONFIG_PATH" --wait 120s

export ANSIBLE_CONFIG="$repo/ansible/ansible.cfg"
export ANSIBLE_ROLES_PATH="$repo/ansible/roles"

ansible-playbook -i "localhost," gate/kind-app/converge.yml
ansible-playbook -i "localhost," gate/kind-app/verify.yml
