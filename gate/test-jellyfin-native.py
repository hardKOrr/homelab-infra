#!/usr/bin/env python3
"""Guard Jellyfin's native method: one pinned release, the shared-operation inputs and the iGPU rule."""

from pathlib import Path

import jinja2
import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/jellyfin"
app = yaml.safe_load((repo / "ansible/vars/app-defaults/jellyfin.yml").read_text())["jellyfin_defaults"]["app"]

# One release for both methods: an exact image tag and exact package versions.
assert app["image"].split(":")[-1] != "latest"
assert app["image"].split(":")[-1].startswith(app["native_version"].split("+")[0])
assert app["native_ffmpeg_version"]
native = (role / "tasks/native.yml").read_text()
assert "https://repo.jellyfin.org/debian" in native
for package in ("jellyfin-server", "jellyfin-web", "jellyfin-ffmpeg8", "jellyfin"):
    assert f'"{package}={{{{ app_config.app.native_' in native, package

# Jellyfin starts on install, so its environment, directories and port come before the packages.
for step in ("Point the service at its directories", "Seed the network configuration"):
    assert native.index(step) < native.index("Install the pinned packages")

# Backup and restore run through the shared operations with the guest's runtime.
assert "pbs_client_runtime: \"{{ app_config.hosting.method" in (role / "tasks/backup.yml").read_text()
assert "recovery_swap_runtime: \"{{ app_config.hosting.method" in (role / "tasks/restore-guest.yml").read_text()

# A native VM refuses an iGPU declaration; an LXC binds the shared nodes.
prepare = (role / "tasks/deploy-prepare.yml").read_text()
assert "Refuse a GPU declaration for native Jellyfin on a VM" in prepare
assert "app_config.hosting.method in ['docker', 'native']" in prepare

# The unit drop-in renders the resolved DRI groups, and resets them when there are none.
env = jinja2.Environment(keep_trailing_newline=True)
env.filters["comment"] = lambda text: "# " + str(text)
template = env.from_string((role / "templates/jellyfin-native.conf.j2").read_text())
with_groups = template.render(ansible_managed="m", _jf_dri_group_ids=["44", "104"], jellyfin_native_env_file="/e")
assert "SupplementaryGroups=44 104" in with_groups and "EnvironmentFile=/e" in with_groups
without = template.render(ansible_managed="m", _jf_dri_group_ids=[], jellyfin_native_env_file="/e")
assert "SupplementaryGroups=\n" in without and "SupplementaryGroups=4" not in without

print("Jellyfin native pins one release, reuses the shared backup and restore and keeps the iGPU on an LXC")
