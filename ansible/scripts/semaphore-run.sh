#!/usr/bin/env bash
# Common Semaphore Shell/Bash task entry point. Configure each mutating template with
# this script and pass the ansible-relative playbook path as its first argument.
set -euo pipefail

[ "$#" -ge 1 ] || { echo "usage: semaphore-run.sh playbooks/PATH.yml [args...]" >&2; exit 2; }

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_dir="$(cd -- "$script_dir/../.." && pwd -P)"

export LAB_REPO="${LAB_REPO:-$repo_dir}"
export LAB_REFRESH=0
export LAB_DOCTOR="${LAB_DOCTOR:-1}"

# Semaphore's repository checkout is not durable. Operators must point this at a
# persistent, service-user-writable mount shared by task executions.
: "${LAB_STATE_DIR:?Semaphore encrypted/non-secret environment must set a durable LAB_STATE_DIR}"

exec "$script_dir/lab-run.sh" "$@"
