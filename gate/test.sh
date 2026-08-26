#!/bin/bash
# Syntax-check gate: --syntax-check over the playbooks under ansible/playbooks/, without
# contacting Proxmox. Run from the repo root:
#   wsl bash -lc 'bash gate/test.sh'          # narrow to the working tree's changes
#   wsl bash -lc 'bash gate/test.sh --all'    # full sweep
#
# A clean tree, an unreadable git, or a change to anything a playbook consumes all resolve
# to the full sweep — see gate/lib-scope.sh for why the narrowing only ever errs
# toward checking more.
set -uo pipefail

# STDIN IS CLOSED FOR THE WHOLE GATE, DELIBERATELY. See the same note in gate/lint.sh:
# the syntax-check pass runs executables it finds in the tree, one of which reads stdin
# and will block forever on an open one. `< /dev/null` turns that into an immediate EOF.
exec < /dev/null

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"

# shellcheck source=lib-scope.sh
. gate/lib-scope.sh
gate_resolve_scope "${1:-}"

cd ansible

# See gate/lint.sh for the ANSIBLE_CONFIG world-writable-directory rationale.
# Derived from $PWD so it works on any machine/checkout location.
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

# Neutralise the Proxmox dynamic inventory (see lint.sh) so no live inventory is touched.
export ANSIBLE_INVENTORY=localhost,

if [ "$gate_scope" = "changed" ]; then
    mapfile -t playbooks < <(cd "$repo" && gate_changed_playbooks)
    echo "Scope: changed ($gate_scope_reason) — ${#playbooks[@]} playbook(s)."
    # A changed set that touches no playbook is a legitimate zero: the focused tests below
    # still run. Only the full sweep may never legitimately be empty.
else
    mapfile -t playbooks < <(find playbooks -name "*.yml")

    # Refuse to report success on an empty check set: an unexpanded find (e.g. a shell-relay
    # quoting hazard that mangles the command before it reaches WSL) must fail loudly, not
    # silently run zero iterations and exit 0.
    if [ "${#playbooks[@]}" -eq 0 ]; then
        echo "ERROR: find playbooks -name *.yml matched zero files; refusing to report a false pass." >&2
        exit 1
    fi
    echo "Scope: full ($gate_scope_reason) — ${#playbooks[@]} playbook(s)."
fi

rc=0

if [ "${#playbooks[@]}" -gt 0 ]; then
    # Each check is a cold interpreter that re-imports every collection, and the checks are
    # independent, so the wall clock here is core-bound rather than work-bound. Output goes
    # to a file per playbook and is replayed in order afterwards: interleaved writes from
    # parallel children are unreadable, and a failure's diagnostic is the whole point.
    GATE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/homelab-gate-test.XXXXXX")"
    trap 'rm -rf -- "$GATE_TMP"' EXIT
    # Overridable so the parallel runner itself can be exercised against a stub, without
    # paying for 33 real interpreters to prove the fan-out and log replay work.
    GATE_ANSIBLE_PLAYBOOK="${GATE_ANSIBLE_PLAYBOOK:-$HOME/.venvs/homelab-ansible/bin/ansible-playbook}"
    export GATE_TMP GATE_ANSIBLE_PLAYBOOK

    gate_check_one() {
        local pb="$1" slug
        slug="${pb//\//__}"
        if "$GATE_ANSIBLE_PLAYBOOK" --syntax-check -i localhost, "$pb" \
            > "$GATE_TMP/$slug.log" 2>&1; then
            return 0
        fi
        : > "$GATE_TMP/$slug.fail"
        return 1
    }
    export -f gate_check_one

    jobs="${GATE_JOBS:-$(nproc 2>/dev/null || echo 4)}"
    echo "Syntax-checking with $jobs parallel job(s)."
    printf '%s\n' "${playbooks[@]}" | xargs -P "$jobs" -I{} bash -c 'gate_check_one "$@"' _ {}

    for pb in "${playbooks[@]}"; do
        slug="${pb//\//__}"
        if [ -e "$GATE_TMP/$slug.fail" ]; then
            rc=1
            echo "== FAIL $pb"
            cat "$GATE_TMP/$slug.log"
        else
            echo "== ok   $pb"
        fi
    done
fi

# Focused unit tests: pure Python, no Ansible startup cost, so they always run in full.
cd "$repo"
bash gate/test-vaultwarden.sh || rc=1
"$HOME/.venvs/homelab-ansible/bin/python" gate/test-callback-output.py || rc=1
bash gate/test-rundeck-job-tree.sh || rc=1
bash gate/test-allocate-ip.sh || rc=1
bash gate/test-registry-forget.sh || rc=1
bash gate/test-maintenance-schedule.sh || rc=1
bash gate/test-proxmox-tags.sh || rc=1
bash gate/test-vmid-from-ip.sh || rc=1
bash gate/test-network-scope.sh || rc=1
exit $rc
