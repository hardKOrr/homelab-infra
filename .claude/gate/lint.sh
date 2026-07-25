#!/bin/bash
# Ansible-lint gate. Invoked as the `lint` gate registered in .isotope/isotope.json
# (see .claude/gate/README.md), from the repo root:
#   wsl bash -lc 'bash .claude/gate/lint.sh'
set -euo pipefail

cd ansible

# The repo lives on /mnt/c (NTFS via WSL9P), which Ansible's own safety check treats as
# "world writable" and silently ignores a cwd-relative ansible.cfg as an ansible.cfg source.
# Without this, ansible/ansible.cfg's roles_path never loads and role-using playbooks (e.g.
# docker/create-docker-host.yml) falsely fail with "role not found". Setting ANSIBLE_CONFIG
# explicitly to an absolute path bypasses the cwd-discovery safety check (Ansible's own
# documented workaround). Derived from $PWD so it works on any machine/checkout location.
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

# Neutralise the Proxmox dynamic inventory: ansible.cfg sets inventory = inventory/, which
# points at the templated community.proxmox plugin needing Proxmox creds. This override
# means the plugin is never invoked and no credentials are required.
export ANSIBLE_INVENTORY=localhost,

# Lint targets the explicit playbooks/roles/tasks/vars dirs, not ".": ansible-lint auto-detects
# a bare "." target as a single *role* here (ansible/ has top-level tasks/, vars/, roles/ dirs,
# matching role-layout heuristics) and silently short-scans ~3 files instead of the full tree.
"$HOME/.venvs/homelab-ansible/bin/ansible-lint" -c .ansible-lint playbooks roles tasks vars
