#!/bin/bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
"$HOME/.venvs/homelab-ansible/bin/python" "$repo/gate/test-deemix-arl-rotation.py"
