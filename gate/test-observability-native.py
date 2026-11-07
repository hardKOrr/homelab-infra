#!/usr/bin/env python3
"""Guard Observability's native method: pinned releases shared with the images, and the
shared-operation inputs."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/observability"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/observability.yml").read_text())["observability_defaults"]
app = defaults["app"]

# One exact release per component, for the images and the native install.
assert app["grafana_image"].endswith(f":{app['grafana_version']}")
assert app["prometheus_image"].endswith(f":v{app['prometheus_version']}")
assert len(app["prometheus_sha256"]) == 64
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
assert "latest" not in app["grafana_image"] + app["prometheus_image"]

install = (role / "tasks/native-install.yml").read_text()
assert "https://apt.grafana.com" in install
assert "grafana={{ app_config.app.grafana_version }}" in install
assert "prometheus_sha256" in install and "prometheus_version" in install

# Bootstrap's node exporter owns the distribution's prometheus account. A native
# deployment must never mutate that running user's identity.
tasks = yaml.safe_load(install)
for kind in ("groups", "users"):
    task = next(t for t in tasks if t["name"].endswith("Create the service " + kind))
    prometheus = next(item for item in task["loop"] if "prometheus" in item["name"])
    assert prometheus["name"] == "observability-prometheus"
    assert prometheus["id"] == "{{ observability_prometheus_uid }}"
unit = (role / "templates/prometheus.service.j2").read_text()
assert "User=observability-prometheus\nGroup=observability-prometheus" in unit

# Both service users must traverse the shared parent before reaching private data.
main = yaml.safe_load((role / "tasks/main.yml").read_text())
parent = next(t for t in main if t["name"] == "Create the shared native data parent")
assert parent["ansible.builtin.file"] == {
    "path": "{{ app_config.app.data_path }}", "state": "directory",
    "owner": "root", "group": "root", "mode": "0711",
}
assert parent["when"] == "observability_native | bool"
assert main.index(parent) < next(i for i, t in enumerate(main) if t["name"] == "Create data directories")

# Backup and restore use the shared files with the guest's runtime.
backup = (role / "tasks/backup.yml").read_text()
assert 'pbs_client_runtime: "{{ app_config.hosting.method' in backup
assert "tasks/backup/pbs-client-backup.yml" in backup
restore = (role / "tasks/restore-guest.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in restore

print("Observability native pins one release per component and reuses the shared backup and restore")
