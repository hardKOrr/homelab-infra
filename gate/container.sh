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
#
# The trap must not swallow a destroy failure with `|| true`: an otherwise green run
# whose cleanup then fails has to fail the gate too, or CI reports success while the
# privileged target (and whatever it started inside itself) is left on the runner's
# Docker host. rc captures whatever exit status the script was already carrying —
# an earlier converge/verify failure — so a passing destroy never hides it, and a
# failing destroy is recorded even when everything before it passed.
rc=0
cleanup() {
    rc=$?
    if ! molecule destroy -s docker-app; then
        echo "gate/container.sh: molecule destroy failed; the platform container may still be present." >&2
        [ "$rc" -eq 0 ] && rc=1
    fi
    exit "$rc"
}
trap cleanup EXIT

molecule create -s docker-app
molecule converge -s docker-app
molecule idempotence -s docker-app
molecule verify -s docker-app
