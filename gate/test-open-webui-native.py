#!/usr/bin/env python3
"""Guard Open WebUI's native method: the pinned PyPI package and the shared-operation inputs."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/open-webui"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/open-webui.yml").read_text())["open_webui_defaults"]

assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
assert defaults["hosting"]["method"] == "docker"
version = defaults["app"]["native_version"]
assert defaults["app"]["image"].endswith(f":v{version}")

native = (role / "tasks/native.yml").read_text()
assert 'open-webui=={{ app_config.app.native_version }}' in native

main = (role / "tasks/main.yml").read_text()
assert 'pbs_client_runtime: "{{ open_webui_runtime }}"' in main
assert "tasks/backup/pbs-client-script.yml" in main
restore = (role / "tasks/restore-docker.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in restore
assert 'recovery_swap_runtime: "{{ open_webui_runtime }}"' in restore
assert "OPEN_WEBUI_RUNTIME" in (role / "templates/open-webui-recovery.env.j2").read_text()

print("Open WebUI native pins the PyPI package and reuses the shared backup and restore")
