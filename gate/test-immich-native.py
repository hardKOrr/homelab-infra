#!/usr/bin/env python3
"""Guard Immich's native method: one pinned release, verified downloads, the shared-operation inputs."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/immich"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/immich.yml").read_text())["immich_defaults"]
app = defaults["app"]

# One release for both methods, and a sha256 for every pinned download.
assert app["image"].endswith(f":v{app['native_version']}")
assert app["machine_learning_image"].split(":")[-1].startswith(f"v{app['native_version']}")
for key in ("source", "node", "uv", "ffmpeg", "countries"):
    assert len(app[f"native_{key}_sha256"]) == 64, key
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])

native = (role / "tasks/native.yml").read_text()
assert "immich-app/immich/archive/refs/tags/v{{ app_config.app.native_version }}.tar.gz" in native
for key in ("source", "node", "uv", "ffmpeg", "countries"):
    assert f"native_{key}_sha256" in native, key

# The service user needs the unit, the environment file and the models before the first start.
assert native.index("Render the service units") < native.index("Enable and start Immich")
assert native.index("Build the pinned release") < native.index("Render the service units")

# The environment file carries the native paths only on the native method.
env = (role / "templates/immich.env.j2").read_text()
assert "{% if immich_native | bool %}" in env and "IMMICH_MACHINE_LEARNING_URL" in env

# Backup, restore, database dump and Remove run through the shared operations with the
# method's runtime.
assert "pbs_client_runtime: \"{{ app_config.hosting.method" in (role / "tasks/backup.yml").read_text()
assert "database_client_runtime: \"{{ app_config.hosting.method" in (role / "tasks/backup-quiesced.yml").read_text()
assert "recovery_swap_runtime: \"{{ app_config.hosting.method" in (role / "tasks/restore-guest.yml").read_text()
assert "in ['docker', 'native']" in (role / "tasks/restore.yml").read_text()
assert "restart_runtime: \"{{ 'native' if immich_native" in (role / "handlers/main.yml").read_text()
units = yaml.safe_load((role / "tasks/remove-units.yml").read_text())[0]["ansible.builtin.set_fact"]["_rm_service_names"]
assert units == ["{{ immich_native_server_service }}", "{{ immich_native_ml_service }}"]
# Backup and restore stop the same two names on both methods: the units are named like the services.
assert yaml.safe_load((role / "defaults/main.yml").read_text())["immich_native_server_service"] == "immich-server"
assert yaml.safe_load((role / "defaults/main.yml").read_text())["immich_native_ml_service"] == "immich-machine-learning"

# A native VM refuses an iGPU declaration; an LXC binds the shared nodes.
prepare = (role / "tasks/deploy-prepare.yml").read_text()
assert "Refuse a GPU declaration for native Immich on a VM" in prepare
assert "app_config.hosting.method in ['docker', 'native']" in prepare

print("Immich native pins one release with verified downloads and reuses the shared backup, restore and remove")
