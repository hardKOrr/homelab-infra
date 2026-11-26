#!/usr/bin/env python3
"""Guard Plex's native method: one pinned release, the shared-operation inputs and the iGPU rule."""

from pathlib import Path

import jinja2
import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/plex"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/plex.yml").read_text())["plex_defaults"]
app = defaults["app"]

# One release for both methods: an exact image tag and the same exact package version.
assert app["image"].split(":")[-1] == app["native_version"] != "latest"
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
native = (role / "tasks/native.yml").read_text()
assert "https://repo.plex.tv/deb" in native
assert '"plexmediaserver={{ app_config.app.native_version }}"' in native

# deb822_repository needs python3-debian first; Plex starts on install, so its user,
# directories and drop-in (with the claim token) come before the package.
assert native.index("python3-debian") < native.index("Add the Plex Media Server APT repository")
for step in ("Create the service user", "Create the project, data", "Render the unit drop-in"):
    assert native.index(step) < native.index("Install the pinned package"), step

# Backup and restore run through the shared operations with the guest's runtime.
assert "pbs_client_runtime: \"{{ app_config.hosting.method" in (role / "tasks/backup.yml").read_text()
assert "recovery_swap_runtime: \"{{ app_config.hosting.method" in (role / "tasks/restore-guest.yml").read_text()

# A native VM refuses an iGPU declaration; an LXC binds the shared nodes.
prepare = (role / "tasks/deploy-prepare.yml").read_text()
assert "Refuse a GPU declaration for native Plex on a VM" in prepare
assert "app_config.hosting.method in ['docker', 'native']" in prepare

# The package ignores PLEX_CLAIM, so Deploy exchanges the token and seeds Preferences.xml
# before the package first starts Plex.
claim = (role / "tasks/native-claim.yml").read_text()
assert "plex.tv/api/claim/exchange" in claim and "PlexOnlineToken=" in claim
# The claim writes Preferences.xml into the state directory, which must exist by then.
assert '"{{ plex_state_path }}"' in native[: native.index("Claim the server")]
assert native.index("Claim the server") < native.index("Install the pinned package")
assert "PLEX_CLAIM" not in (role / "templates/plex-native.conf.j2").read_text()

# The drop-in resets the DRI groups when there are none.
env = jinja2.Environment(keep_trailing_newline=True)
env.filters["comment"] = lambda text: "# " + str(text)
template = env.from_string((role / "templates/plex-native.conf.j2").read_text())
ctx = dict(ansible_managed="m", app_config={"app": {"config_path": "/opt/p/config"}}, homelabinfra_config={})
with_groups = template.render(_plex_dri_group_ids=["44", "104"], **ctx)
assert "SupplementaryGroups=44 104" in with_groups
assert "/opt/p/config/Library/Application Support" in with_groups
assert "SupplementaryGroups=\n" in template.render(_plex_dri_group_ids=[], **ctx)

print("Plex native pins one release, reuses the shared backup and restore and keeps the iGPU on an LXC")
