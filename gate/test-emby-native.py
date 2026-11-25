#!/usr/bin/env python3
"""Guard Emby's native method: one pinned release, the shared-operation inputs and the iGPU rule."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/emby"
app = yaml.safe_load((repo / "ansible/vars/app-defaults/emby.yml").read_text())["emby_defaults"]["app"]

# One exact release for both methods, and a verified .deb.
assert app["image"].endswith(f":{app['native_version']}")
assert len(app["native_deb_sha256"]) == 64
native = (role / "tasks/native.yml").read_text()
assert "emby-server-deb_{{ app_config.app.native_version }}_amd64.deb" in native
assert "native_deb_sha256" in native

# Emby starts on install, so its environment file and port come before the package.
assert native.index("Seed the server configuration") < native.index("Install the .deb")
assert native.index("Point the service at its config directory") < native.index("Install the .deb")

# Backup and restore run through the shared operations with the guest's runtime.
assert "pbs_client_runtime: \"{{ app_config.hosting.method" in (role / "tasks/backup.yml").read_text()
assert "recovery_swap_runtime: \"{{ app_config.hosting.method" in (role / "tasks/restore-guest.yml").read_text()

# A native VM refuses an iGPU declaration; an LXC binds the shared nodes.
prepare = (role / "tasks/deploy-prepare.yml").read_text()
assert "Refuse a GPU declaration for native Emby on a VM" in prepare
assert "app_config.hosting.method in ['docker', 'native']" in prepare

# The unit drop-in renders the resolved DRI groups, and resets them when there are none.
import jinja2

env = jinja2.Environment(extensions=[], keep_trailing_newline=True)
env.filters["comment"] = lambda text: "# " + str(text)
template = env.from_string((role / "templates/emby-native.conf.j2").read_text())
with_groups = template.render(ansible_managed="m", _emby_dri_group_ids=["44", "104"])
assert "SupplementaryGroups=44 104" in with_groups and "[Service]" in with_groups
without = template.render(ansible_managed="m", _emby_dri_group_ids=[])
assert "SupplementaryGroups=\n" in without and "SupplementaryGroups=4" not in without

print("Emby native pins one release, reuses the shared backup and restore and keeps the iGPU on an LXC")
