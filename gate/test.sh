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

# Every check below is independent, so both phases run in parallel. Output goes to a file
# per check and is replayed in order afterwards: interleaved writes from parallel children
# are unreadable, and a failure's diagnostic is the whole point.
GATE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/homelab-gate-test.XXXXXX")"
trap 'rm -rf -- "$GATE_TMP"' EXIT
GATE_ANSIBLE_PLAYBOOK="${GATE_ANSIBLE_PLAYBOOK:-$HOME/.venvs/homelab-ansible/bin/ansible-playbook}"
GATE_PYTHON="$HOME/.venvs/homelab-ansible/bin/python"
export GATE_TMP GATE_ANSIBLE_PLAYBOOK GATE_PYTHON
jobs="${GATE_JOBS:-$(nproc 2>/dev/null || echo 4)}"

# Runs the command after $1 (the item's name) with its log in $GATE_TMP; a .fail
# marker records failure.
gate_run_one() {
    local item="$1" slug="${1//\//__}"
    shift
    if "$@" > "$GATE_TMP/$slug.log" 2>&1; then
        return 0
    fi
    : > "$GATE_TMP/$slug.fail"
    return 1
}
gate_check_one() {
    gate_run_one "$1" "$GATE_ANSIBLE_PLAYBOOK" --syntax-check -i localhost, "$1"
}
gate_suite_one() {
    case "$1" in
        *.sh) gate_run_one "$1" bash "$1" ;;
        *) gate_run_one "$1" "$GATE_PYTHON" "$1" ;;
    esac
}
export -f gate_run_one gate_check_one gate_suite_one

# Replays each item's verdict and, for a failure, its whole log.
gate_report() {
    local item slug
    for item in "$@"; do
        slug="${item//\//__}"
        if [ -e "$GATE_TMP/$slug.fail" ]; then
            rc=1
            echo "== FAIL $item"
            cat "$GATE_TMP/$slug.log"
        else
            echo "== ok   $item"
        fi
    done
}

echo "Syntax-checking with $jobs parallel job(s)."
printf '%s\n' "${playbooks[@]}" | xargs -P "$jobs" -I{} bash -c 'gate_check_one "$@"' _ {}
gate_report "${playbooks[@]}"

# Every gate/test-* script is a suite, so a new one runs without being listed here.
cd "$repo"
mapfile -t suites < <(find gate -maxdepth 1 -name 'test-*' \( -name '*.py' -o -name '*.sh' \) | sort)
echo "Running ${#suites[@]} logic suites with $jobs parallel job(s)."
printf '%s\n' "${suites[@]}" | xargs -P "$jobs" -I{} bash -c 'gate_suite_one "$@"' _ {}
gate_report "${suites[@]}"
exit $rc
