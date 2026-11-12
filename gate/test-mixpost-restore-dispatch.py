#!/usr/bin/env python3
"""Mixpost's source application key must reach the restore host.

restore-inputs.yml sets restore_source_mixpost_secrets on localhost; the shared restore
dispatch runs the role on the target host, so it has to forward the fact explicitly.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
dispatch = (ROOT / "ansible/playbooks/maintenance/restore-app.yml").read_text(encoding="utf-8")
inputs = (ROOT / "ansible/roles/mixpost/tasks/restore-inputs.yml").read_text(encoding="utf-8")
guest = (ROOT / "ansible/roles/mixpost/tasks/restore-guest.yml").read_text(encoding="utf-8")

assert "restore_source_mixpost_secrets:" in inputs
assert (
    "restore_source_mixpost_secrets: \"{{ hostvars['localhost'].restore_source_mixpost_secrets"
    in dispatch
), "restore-app.yml does not forward the source Mixpost key to the restore host"
assert "restore_source_mixpost_secrets.app_key" in guest
print("Mixpost restore dispatch forwards the source key")
