"""Jinja tests that classify how an app's guest is hosted.

`app_config is iso_appliance` is the one definition of an ISO-installed appliance: a
guest with no cloud-init, SSH or guest-agent contract. Backup, restore, removal, route
rebuilds and Lab Status all ask this instead of repeating the condition.
"""
from __future__ import annotations


def iso_appliance(app_config):
    config = app_config if isinstance(app_config, dict) else {}
    proxmox = config.get("proxmox") or {}
    install = config.get("install") or {}
    return bool(proxmox.get("installer")) or install.get("method") == "official-iso"


class TestModule:
    def tests(self):
        return {"iso_appliance": iso_appliance}
