#!/bin/bash
# Lint gate. Run from anywhere: bash gate/lint.sh
# ansible-lint over ansible/{playbooks,roles,tasks,vars}, Jinja parsing, repository links,
# fixture secrets and GitHub workflow policy. Arguments are accepted and ignored.
set -euo pipefail

# Stdin is closed on purpose: ansible-lint executes scripts it finds under roles/*/files/
# as inventory scripts, and qbittorrent's webui-password.py blocks forever on an open stdin.
exec < /dev/null

# Quiet unless a person is watching or CI runs it: stdout that is not a terminal (an LLM
# agent's tool call) selects GATE_QUIET=1; GATE_QUIET=0 forces the full output.
if [ -z "${GATE_QUIET+x}" ] && [ -z "${CI:-}" ] && [ ! -t 1 ]; then
    GATE_QUIET=1
fi
[ "${GATE_QUIET:-}" != 0 ] || GATE_QUIET=
export GATE_QUIET

# GATE_QUIET=1 keeps the tool chatter out of the output: a passing run prints each check's
# one-line summary, a failing run prints everything.
if [ -n "${GATE_QUIET:-}" ] && [ -z "${GATE_LINT_INNER:-}" ]; then
    log="$(mktemp "${TMPDIR:-/tmp}/homelab-gate-lint.XXXXXX")"
    trap 'rm -f -- "$log"' EXIT
    if GATE_LINT_INNER=1 bash "${BASH_SOURCE[0]}" > "$log" 2>&1; then
        grep -E '^(Passed:|jinja-parse:|check-)' "$log"
        exit 0
    fi
    cat "$log"
    exit 1
fi

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
