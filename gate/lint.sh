#!/bin/bash
# Ansible-lint gate. Run from the repo root:
#   wsl bash -lc 'bash gate/lint.sh'          # narrow to the working tree's changes
#   wsl bash -lc 'bash gate/lint.sh --all'    # full sweep
#
# A clean tree, an unreadable git, or a change to anything a playbook consumes all resolve
# to the full sweep — see gate/lib-scope.sh.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"

# shellcheck source=lib-scope.sh
. gate/lib-scope.sh
gate_resolve_scope "${1:-}"

# Narrowed runs lint the changed files themselves. ansible-lint's rules are per-file, so a
# file list is a faithful subset of the full run — unlike the target *directories* below,
# which have to stay directories for the auto-detection reason documented there.
lint_targets=(playbooks roles tasks vars)
if [ "$gate_scope" = "changed" ]; then
    mapfile -t lint_targets < <(gate_changed_ansible_yaml | grep -E '^(playbooks|roles|tasks|vars)/' || true)
fi

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
if [ "${#lint_targets[@]}" -eq 0 ]; then
    echo "Scope: changed ($gate_scope_reason) — no linted ansible files touched; skipping ansible-lint."
else
    echo "Scope: $gate_scope ($gate_scope_reason) — ${#lint_targets[@]} target(s)."
    "$HOME/.venvs/homelab-ansible/bin/ansible-lint" -c .ansible-lint "${lint_targets[@]}"
fi

# Compile every Jinja expression in the tree. ansible-lint above and --syntax-check in
# test.sh both parse YAML without ever templating a string, so a malformed expression —
# most cheaply, a YAML `#` comment indented inside a `{{ }}` block, which Jinja reads as
# expression source — reaches a live provision before anything notices. See the docstring
# in jinja-parse.py for the run that proved it.
cd ..
"$HOME/.venvs/homelab-ansible/bin/python" gate/jinja-parse.py ansible
