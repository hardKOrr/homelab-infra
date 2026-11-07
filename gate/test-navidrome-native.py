#!/usr/bin/env python3
"""Guard Navidrome's native method: one pinned release and the shared-operation inputs."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/navidrome"
app = yaml.safe_load((repo / "ansible/vars/app-defaults/navidrome.yml").read_text())["navidrome_defaults"]["app"]

# One exact release for both methods, and a verified .deb.
assert app["image"].endswith(f":{app['native_version']}")
assert len(app["native_deb_sha256"]) == 64
native = (role / "tasks/native.yml").read_text()
assert "navidrome_{{ app_config.app.native_version }}_linux_amd64.deb" in native
assert "native_deb_sha256" in native

# Backup runs through the shared PBS client with the guest's runtime.
backup = (role / "tasks/backup.yml").read_text()
assert "pbs_client_runtime: \"{{ app_config.hosting.method" in backup
assert "tasks/backup/pbs-client-backup.yml" in backup

# The music path is read-only to the native unit.
assert "ReadOnlyPaths={{ _navidrome_music_path }}" in (role / "templates/navidrome-native.conf.j2").read_text()

print("Navidrome native pins one release and reuses the shared backup and restore")
