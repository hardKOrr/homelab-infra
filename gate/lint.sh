#!/bin/bash
# Lint gate. Run from anywhere: bash gate/lint.sh
# ansible-lint over ansible/{playbooks,roles,tasks,vars}, Jinja parsing, repository links,
# fixture secrets and GitHub workflow policy. Arguments are accepted and ignored.
set -euo pipefail

# Stdin is closed on purpose: ansible-lint executes scripts it finds under roles/*/files/
# as inventory scripts, and qbittorrent's webui-password.py blocks forever on an open stdin.
exec < /dev/null

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
py="$HOME/.venvs/homelab-ansible/bin/python"

cd "$repo/ansible"
# A world-writable checkout (e.g. NTFS via WSL) makes Ansible ignore a cwd ansible.cfg;
# an absolute ANSIBLE_CONFIG bypasses that check.
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
# Never invoke the Proxmox dynamic inventory from the gate.
export ANSIBLE_INVENTORY="$repo/gate/fixtures/localhost.ini"
# Explicit directories: a bare "." is auto-detected as a single role and short-scanned.
"$HOME/.venvs/homelab-ansible/bin/ansible-lint" -c .ansible-lint playbooks roles tasks vars

cd "$repo"
"$py" gate/jinja-parse.py ansible
"$py" gate/check-links.py
"$py" gate/check-fixture-secrets.py
"$py" gate/check-workflow-policy.py
