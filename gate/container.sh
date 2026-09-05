#!/bin/bash
# Container role integration harness. Runs the ansible/molecule/docker-app scenario:
# converge, idempotence (second converge reports no changes), verify (rendered
# Compose input + a real readiness probe), then teardown. See
# ansible/molecule/docker-app/README.md for scope and rationale.
#
# Usage (from the repo root):
#   bash gate/container.sh
#
# Requires a local Docker daemon (already present on GitHub-hosted Ubuntu runners;
# see .github/workflows/gate.yml's "container" job). Installs nothing into that
# daemon's global configuration — the scenario's platform container is the only
# resource Molecule creates, and it owns everything the converge starts inside it.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"

CONTAINER_VENV="${CONTAINER_VENV:-$HOME/.venvs/homelab-molecule}"

if [ ! -x "$CONTAINER_VENV/bin/molecule" ]; then
    echo "Bootstrapping container harness venv at $CONTAINER_VENV"
    python3 -m venv "$CONTAINER_VENV"
    "$CONTAINER_VENV/bin/pip" install --upgrade pip
    "$CONTAINER_VENV/bin/pip" install -r gate/requirements-container.txt
fi

export PATH="$CONTAINER_VENV/bin:$PATH"

for attempt in 1 2 3; do
    ansible-galaxy collection install -r ansible/requirements.yml && break
    if [ "$attempt" -eq 3 ]; then
        exit 1
    fi
    sleep "$((attempt * 10))"
done

# ansible.posix is Molecule's own Docker driver dependency, not a repository
# collection — kept out of ansible/requirements.yml, which pins only what the
# application/provisioning code imports.
ansible-galaxy collection install ansible.posix

# Molecule resolves "molecule/<scenario>" relative to cwd, so it runs from ansible/
# (the scenario's project directory) naming the scenario explicitly, rather than
# from inside the scenario directory itself.
cd ansible

# Teardown always runs, including after a failed converge: molecule destroy removes
# only the one platform container this scenario created (named docker-app-target),
# so cleanup cannot reach an unrelated runner container. See "Recovery needs" in the
# tracker issue this harness answers.
trap 'molecule destroy -s docker-app || true' EXIT

molecule create -s docker-app
molecule converge -s docker-app
molecule idempotence -s docker-app
molecule verify -s docker-app
