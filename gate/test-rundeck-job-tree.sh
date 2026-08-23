#!/bin/bash
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python="${GATE_PYTHON:-$HOME/.venvs/homelab-ansible/bin/python}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

"$python" "$repo/rundeck/render-job.py" --check "$repo/rundeck/jobs"

for job in "$repo"/rundeck/jobs/*.yaml; do
    rendered="$($python "$repo/rundeck/render-job.py" "$job")"
    [ -n "$rendered" ] || {
        echo "ERROR: renderer returned an empty document for $job" >&2
        exit 1
    }
done

# Every rendered step must be a runnable script. A per-application template carries %TOKEN%
# placeholders inside its shell, so an unsubstituted token would reach the runner as a
# literal and fail there rather than here.
"$python" - "$repo" "$work" <<'PY'
import pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / "rundeck"))
import importlib.util

repo, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("render_job", repo / "rundeck" / "render-job.py")
render_job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_job)

failures = []
for path in sorted((repo / "rundeck" / "jobs").glob("*.yaml")):
    for job in render_job.render(path):
        for key in ("name", "group"):
            if "%" in str(job.get(key, "")):
                failures.append(f"{path.name}: {job.get('name')} kept a placeholder in {key}")
        for index, command in enumerate(job.get("sequence", {}).get("commands", [])):
            script = command.get("script")
            if not script:
                continue
            if "%SLUG%" in script or "%NAME%" in script or "%STACK%" in script:
                failures.append(f"{path.name}: {job['name']} kept a placeholder in its script")
            target = work / f"{job['uuid']}-{index}.sh"
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(script)
            result = subprocess.run(["bash", "-n", str(target)], capture_output=True, text=True)
            if result.returncode != 0:
                failures.append(f"{path.name}: {job['name']} step {index}: {result.stderr.strip()}")

if failures:
    print("\n".join(f"ERROR: {failure}" for failure in failures), file=sys.stderr)
    sys.exit(1)
PY

# The instance lists the generated job forms read must be produced from the same catalog.
"$python" "$repo/ansible/scripts/app-instances.py" --repo "$repo" --out "$work/instances"
for slug in $("$python" -c "
import sys, yaml
document = yaml.safe_load(open(sys.argv[1], encoding='utf-8'))
print(' '.join(document['applications']))
" "$repo/catalog/applications.yml"); do
    [ -f "$work/instances/$slug.json" ] || {
        echo "ERROR: no instance list was published for $slug" >&2
        exit 1
    }
done

echo "Rundeck render: all jobs ok"
