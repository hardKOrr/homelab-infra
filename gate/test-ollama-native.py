#!/usr/bin/env python3
"""Guard Ollama's native method: the pinned release archive and the shared-operation inputs."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/ollama"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/ollama.yml").read_text())["ollama_defaults"]

assert "native-lxc" in defaults["hosting"]["supported"] and "native-vm" in defaults["hosting"]["supported"]
assert defaults["hosting"]["method"] == "docker"
app = defaults["app"]
version = app["native_version"]
assert version.count(".") == 2 and app["image"] == f"ollama/ollama:{version}"
assert len(app["native_archive_sha256"]) == 64

native = (role / "tasks/native.yml").read_text()
assert "ollama-linux-amd64.tar.zst" in native and "native_archive_sha256" in native

backup = (role / "tasks/backup.yml").read_text()
assert "tasks/backup/pbs-client-backup.yml" in backup
assert "pbs_client_runtime: \"{{ app_config.hosting.method" in backup
restore = (role / "tasks/restore-guest.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in restore
assert "recovery_swap_runtime: \"{{ app_config.hosting.method" in restore

print("Ollama native pins the release archive and reuses the shared backup and restore")
