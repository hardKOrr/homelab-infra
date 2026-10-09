#!/bin/bash
# Test gate: --syntax-check every playbook, then the logic suites.
set -uo pipefail

# STDIN IS CLOSED FOR THE WHOLE GATE, DELIBERATELY. See the same note in gate/lint.sh:
# the syntax-check pass runs executables it finds in the tree, one of which reads stdin
# and will block forever on an open one. `< /dev/null` turns that into an immediate EOF.
exec < /dev/null

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"

cd ansible

# See gate/lint.sh for the ANSIBLE_CONFIG world-writable-directory rationale.
# Derived from $PWD so it works on any machine/checkout location.
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

# Neutralise the Proxmox dynamic inventory (see lint.sh) so no live inventory is touched.
export ANSIBLE_INVENTORY="$repo/gate/fixtures/localhost.ini"

mapfile -t playbooks < <(find playbooks -name "*.yml")

# Refuse to report success when no playbooks were found.
if [ "${#playbooks[@]}" -eq 0 ]; then
    echo "ERROR: find playbooks -name *.yml matched zero files; refusing to report a false pass." >&2
    exit 1
fi

rc=0

if [ "${#playbooks[@]}" -gt 0 ]; then
    # Each check is a cold interpreter that re-imports every collection, and the checks are
    # independent, so the wall clock here is core-bound rather than work-bound. Output goes
    # to a file per playbook and is replayed in order afterwards: interleaved writes from
    # parallel children are unreadable, and a failure's diagnostic is the whole point.
    GATE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/homelab-gate-test.XXXXXX")"
    trap 'rm -rf -- "$GATE_TMP"' EXIT
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

cd "$repo"
py="$HOME/.venvs/homelab-ansible/bin/python"
bash gate/test-allocate-ip.sh || rc=1
bash gate/test-vmid-from-ip.sh || rc=1
"$py" gate/test-config.py || rc=1
"$py" gate/test-rundeck-yaml.py || rc=1
"$py" gate/test-template-rendering.py || rc=1
"$py" gate/test-vaultwarden-login.py || rc=1
"$py" gate/test-actual-budget-password.py || rc=1
"$py" gate/test-recovery-dispatch.py || rc=1
"$py" gate/test-plex-recovery.py || rc=1
"$py" gate/test-tautulli-restore.py || rc=1
"$py" gate/test-proxmox-task.py || rc=1
"$py" gate/test-decommission-retire.py || rc=1
exit $rc
