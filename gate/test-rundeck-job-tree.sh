#!/bin/bash
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python="${GATE_PYTHON:-$HOME/.venvs/homelab-ansible/bin/python}"

"$python" "$repo/rundeck/render-job.py" --check "$repo/rundeck/jobs"

for job in "$repo"/rundeck/jobs/*.yaml; do
    rendered="$($python "$repo/rundeck/render-job.py" "$job")"
    [ -n "$rendered" ] || {
        echo "ERROR: renderer returned an empty document for $job" >&2
        exit 1
    }
done

echo "Rundeck render: all jobs ok"
