#!/usr/bin/env python3
"""Guard Plane's native method: pinned sources, one unit per service, shared operations."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/plane"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/plane.yml").read_text())["plane_defaults"]

assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
assert defaults["hosting"]["method"] == "docker"
app = defaults["app"]
assert app["release"] == "v1.4.2"
for pin in ("native_source_sha256", "native_object_storage_sha256"):
    assert len(app[pin]) == 64, pin

role_defaults = yaml.safe_load((role / "defaults/main.yml").read_text())
# Web, admin, space and live are built; web and admin are served by nginx, space and live run.
assert set(role_defaults["plane_native_builds"]) == {"web", "admin", "space", "live"}
units = role_defaults["plane_native_units"]
assert set(units) == {"plane-minio", "plane-api", "plane-worker", "plane-beat", "plane-live", "plane-space"}
assert role_defaults["plane_native_services"] == [*units, "nginx"]

build = (role / "tasks/native-build.yml").read_text()
assert "{{ app_config.app.native_source_sha256 }}" in build
assert "--frozen-lockfile" in build and "{{ app_config.app.release }}.tar.gz" in build

# The units are rendered from one template and every service reads the shared environment file.
template = (role / "templates/plane.service.j2").read_text()
assert "EnvironmentFile={{ plane_env_file }}" in template and "User={{ plane_native_user }}" in template
assert "tasks/backup/pbs-client-script.yml" in (role / "tasks/recovery-timer.yml").read_text()
assert 'pbs_client_runtime: "{{ plane_runtime }}"' in (role / "tasks/recovery-timer.yml").read_text()

restore = (role / "tasks/restore-docker.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in restore
assert 'recovery_swap_runtime: "{{ plane_runtime }}"' in restore
assert "plane_native_services" in restore
# The broker user is recreated after the keys change, on restore and on rollback.
assert "native-rabbitmq.yml" in (role / "tasks/restore-keys.yml").read_text()
assert "PLANE_RUNTIME" in (role / "templates/plane-recovery.env.j2").read_text()

print("Plane native builds the pinned source, runs one unit per service and reuses the shared recovery")
