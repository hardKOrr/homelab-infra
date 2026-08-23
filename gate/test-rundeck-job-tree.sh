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

# Multi-estate rendering has no fixture in the gitignored config/ tree. Supply one here so
# the default instance, explicit estate choice and rollback exclusions stay regression-tested.
multi_estate = work / "infrastructure.yml"
multi_estate.write_text(
    "domains:\n"
    "  personal:\n"
    "    domain: personal.example.test\n"
    "    default: true\n"
    "  foxglove:\n"
    "    domain: foxglove.example.test\n",
    encoding="utf-8",
)
render_job.INFRASTRUCTURE_CONFIG = multi_estate

deploy_radarr = render_job.render(repo / "rundeck" / "jobs" / "deploy-radarr.yaml")[0]
radarr_instance = next(option for option in deploy_radarr["options"] if option["name"] == "instance")
assert radarr_instance["value"] == "radarr-personal"
assert radarr_instance["valuesUrl"].endswith("/radarr.json")

# An application whose own defaults route it to another estate is prefilled for THAT
# estate, so the offered name never contradicts the application's configuration.
deploy_mixpost = render_job.render(repo / "rundeck" / "jobs" / "deploy-mixpost.yaml")[0]
mixpost_instance = next(
    option for option in deploy_mixpost["options"] if option["name"] == "instance"
)
assert mixpost_instance["value"] == "mixpost-foxglove", mixpost_instance["value"]

# Every routed application declares its published hostname, so an estate-suffixed instance
# name can never reach a URL. Only unrouted applications may leave it out.
defaults_dir = repo / "ansible" / "vars" / "app-defaults"
for slug, app in render_job.load_applications().items():
    defaults = render_job.app_defaults_of(slug)
    routing = defaults.get("routing") or {}
    if routing.get("proxy", "none") == "none":
        continue
    assert routing.get("subdomain"), (
        f"app-defaults/{slug}.yml declares no routing.subdomain, so its hostname would"
        " follow the instance name"
    )

# scope is required, not defaulted: an unclassified application must not land silently on
# the shared side of the estate boundary.
catalog_text = (repo / "catalog" / "applications.yml").read_text(encoding="utf-8")
try:
    render_job.load_document  # touch, so a rename of the loader is caught here too
    original = render_job.APPLICATION_CATALOG
    unscoped = work / "unscoped-catalog.yml"
    unscoped.write_text(
        catalog_text.replace("    scope: estate\n", "\n", 1), encoding="utf-8"
    )
    render_job.APPLICATION_CATALOG = unscoped
    try:
        render_job.load_applications()
    except ValueError as error:
        assert "scope must be declared" in str(error), str(error)
    else:
        raise AssertionError("an application with no scope was accepted")
finally:
    render_job.APPLICATION_CATALOG = original

configure_radarr = next(
    job for job in render_job.render(repo / "rundeck" / "jobs" / "configure-app.yaml")
    if job["name"] == "Configure Radarr"
)
estate_option = next(option for option in configure_radarr["options"] if option["name"] == "estate")
assert estate_option["required"] is True
assert estate_option["value"] == "personal"
assert estate_option["enforced"] is True

configure_caddy = next(
    job for job in render_job.render(repo / "rundeck" / "jobs" / "configure-app.yaml")
    if job["name"] == "Configure Caddy"
)
assert all(option["name"] != "estate" for option in configure_caddy["options"])

rollback_names = {
    job["name"] for job in render_job.render(repo / "rundeck" / "jobs" / "rollback-container.yaml")
}
assert "Rollback Radarr" in rollback_names
assert "Rollback Authentik" not in rollback_names
assert "Rollback Observability" not in rollback_names
rollback_radarr = next(
    job for job in render_job.render(repo / "rundeck" / "jobs" / "rollback-container.yaml")
    if job["name"] == "Rollback Radarr"
)
rollback_script = rollback_radarr["sequence"]["commands"][0]["script"]
assert 'app=radarr' in rollback_script and 'stack=' not in rollback_script

multi_estate.write_text(
    "domains:\n"
    "  personal: {domain: personal.example.test}\n"
    "  foxglove: {domain: foxglove.example.test}\n",
    encoding="utf-8",
)
try:
    render_job.estate_context()
except ValueError as error:
    assert "exactly one default: true" in str(error)
else:
    raise AssertionError("multi-estate rendering accepted an order-dependent default")
multi_estate.write_text(
    "domains:\n"
    "  personal:\n"
    "    domain: personal.example.test\n"
    "    default: true\n"
    "  foxglove:\n"
    "    domain: foxglove.example.test\n",
    encoding="utf-8",
)

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

# The option publisher must label every estate and reject an unnamed estate-scoped instance.
"$python" - "$repo" "$work" <<'PY'
import importlib.util, pathlib, sys, yaml

repo, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location(
    "app_instances", repo / "ansible" / "scripts" / "app-instances.py"
)
app_instances = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_instances)

applications = app_instances.load_applications(repo / "catalog" / "applications.yml")
estates = app_instances.load_estates(work / "infrastructure.yml")
fixture = work / "fixture"
(fixture / "config" / "apps").mkdir(parents=True)
(fixture / "config" / "apps" / "radarr-personal.yml").write_text(
    "routing:\n  estate: personal\n", encoding="utf-8"
)
(fixture / "config" / "apps" / "radarr-foxglove-4k.yml").write_text(
    "routing:\n  estate: foxglove\n", encoding="utf-8"
)
instances, unmatched, invalid = app_instances.collect(fixture, applications, estates)
assert not unmatched and not invalid
assert {entry["value"] for entry in instances["radarr"]} == {
    "radarr-personal", "radarr-foxglove-4k"
}
assert {entry["name"] for entry in instances["radarr"]} == {
    "radarr-personal — personal", "radarr-foxglove-4k — foxglove"
}

(fixture / "config" / "apps" / "radarr.yml").write_text("{}\n", encoding="utf-8")
_, _, invalid = app_instances.collect(fixture, applications, estates)
assert invalid and "radarr-personal" in invalid[0]
PY

# Config validation must reject an order-dependent default before any job can run.
mkdir -p "$work/doctor/apps"
cp "$repo/config.example/proxmox.yml" "$work/doctor/proxmox.yml"
"$python" - "$work/doctor/infrastructure.yml" <<'PY'
import pathlib, sys
pathlib.Path(sys.argv[1]).write_text(
    "domains:\n"
    "  personal: {domain: personal.example.test}\n"
    "  foxglove: {domain: foxglove.example.test}\n"
    "reverse_proxy: {provider: none}\n"
    "sso: {provider: none}\n"
    "notifications: {provider: none}\n"
    "dns: {provider: none}\n"
    "backups: {datastore_path: /backup}\n",
    encoding="utf-8",
)
PY
if doctor_output="$(PROXMOX_API_TOKEN=test VAULTWARDEN_ADMIN_TOKEN=test \
    bash "$repo/ansible/scripts/config-doctor.sh" "$work/doctor" 2>&1)"; then
    echo "ERROR: config-doctor accepted a multi-estate map with no explicit default" >&2
    exit 1
fi
grep -q "multi-estate map must declare exactly one default: true" <<<"$doctor_output"

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
