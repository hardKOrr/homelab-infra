#!/usr/bin/env python3
"""Prove rendered Maintainerr restore Jobs mount the workload PVC."""
from pathlib import Path
import sys
import yaml
from jinja2 import Environment, StrictUndefined

root = Path(__file__).resolve().parents[1]
environment = Environment(undefined=StrictUndefined)
context = {
    "instance": "maintainerr",
    "app_config": {"app": {"storage": 1, "port": 6246, "image": "ghcr.io/maintainerr/maintainerr:latest"}, "backup": {"client": {"image": "buildpack-deps:bookworm-curl", "key_url": "key", "repository": "repo", "suite": "bookworm", "component": "main"}}},
    "homelabinfra_config": {"timezone": "UTC"},
    "maintainerr_fqdn": "maintainerr.example.test",
    "client": {"image": "buildpack-deps:bookworm-curl", "key_url": "key", "repository": "repo", "suite": "bookworm", "component": "main"},
    "_js_r_job_name": "maintainerr-restore-plan", "_js_r_target": "maintainerr", "_js_r_mode": "plan", "_js_r_timeout": 900, "_js_r_secret": "maintainerr-restore", "_js_r_backup_id": "maintainerr", "_js_r_snapshot": "", "k8s_pbs_repository": "pbs@host:store", "k8s_pbs_fingerprint": "fingerprint",
}
def render(path):
    return list(yaml.safe_load_all(environment.from_string(path.read_text()).render(**context)))
workload = render(root / "ansible/roles/maintainerr/templates/manifest.yaml.j2")
restore = render(root / "ansible/roles/maintainerr/templates/restore-job.yaml.j2")
workload_pvc = next(item["metadata"]["name"] for item in workload if item and item["kind"] == "PersistentVolumeClaim")
restore_pvc = next(volume["persistentVolumeClaim"]["claimName"] for volume in restore[0]["spec"]["template"]["spec"]["volumes"] if "persistentVolumeClaim" in volume)
if restore_pvc != workload_pvc:
    sys.exit(f"ERROR: Maintainerr restore PVC {restore_pvc!r} does not match workload PVC {workload_pvc!r}.")
print("Maintainerr rendered restore PVC contract passed.")
