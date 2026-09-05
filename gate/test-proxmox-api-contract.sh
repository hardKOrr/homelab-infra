#!/usr/bin/env bash
# Stateful Proxmox API contract tests: see gate/test-proxmox-api-contract.py and
# gate/proxmox_mock.py for what is and is not asserted. Drives the real
# community.proxmox.proxmox module and the real ansible/inventory/proxmox.yml dynamic
# inventory against a job-local HTTPS mock bound to 127.0.0.1 on an ephemeral port; no
# Proxmox or provider endpoint, real or otherwise, is contacted, and no state (or the
# mock's throwaway TLS cert) outlives the Python process below.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

python="$HOME/.venvs/homelab-ansible/bin/python"
if [ ! -x "$python" ]; then
    python="python3"
fi

"$python" "$repo/gate/test-proxmox-api-contract.py"
