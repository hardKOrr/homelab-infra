#!/usr/bin/env python3
"""Keep duplicate Proxmox guest names distinct in Ansible inventory."""

import importlib.util
from pathlib import Path
import sys
import types

from ansible.errors import AnsibleError


class Inventory:
    def __init__(self):
        self.hosts = {}
        self.groups = {}

    def add_host(self, name):
        self.hosts.setdefault(name, {})

    def set_variable(self, host, name, value):
        self.hosts.setdefault(host, {})[name] = value

    def add_group(self, name):
        self.groups.setdefault(name, set())

    def add_child(self, group, host):
        self.groups.setdefault(group, set()).add(host)


class UpstreamInventory:
    """Small pinned-plugin stand-in for the parent methods this wrapper extends."""

    def __init__(self):
        self.inventory = Inventory()
        self.nodes = []
        self.resources = []
        self.pools = []
        self.pool_members = {}
        self.proxmox_url = "https://proxmox.invalid"

    def _get_nodes(self):
        return self.nodes

    def _get_json(self, url):
        assert url.endswith("/api2/json/cluster/resources?type=vm")
        return self.resources

    def _handle_item(self, _node, _type, item):
        host = item["name"]
        self.inventory.add_host(host)
        self.inventory.set_variable(host, self._fact("name"), host)
        return host

    @staticmethod
    def _fact(name):
        return "proxmox_" + name

    @staticmethod
    def _group(name):
        return "proxmox_" + name

    def _get_pools(self):
        return self.pools

    def _get_members_per_pool(self, poolid):
        return self.pool_members[poolid]


def load_plugin():
    """Import the production wrapper against the narrow upstream API it uses."""
    module_names = (
        "ansible_collections",
        "ansible_collections.community",
        "ansible_collections.community.proxmox",
        "ansible_collections.community.proxmox.plugins",
        "ansible_collections.community.proxmox.plugins.inventory",
    )
    for name in module_names:
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    parent = types.ModuleType(
        "ansible_collections.community.proxmox.plugins.inventory.proxmox"
    )
    parent.DOCUMENTATION = "name: proxmox"
    parent.InventoryModule = UpstreamInventory
    sys.modules[parent.__name__] = parent

    plugin_path = (
        Path(__file__).resolve().parents[1]
        / "ansible/inventory_plugins/homelab_proxmox.py"
    )
    spec = importlib.util.spec_from_file_location("homelab_proxmox_test", plugin_path)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin.InventoryModule


def resource(name, vmid, *, kind="qemu", template=0):
    return {"name": name, "vmid": vmid, "type": kind, "template": template}


def main():
    plugin_type = load_plugin()
    plugin = plugin_type()
    plugin.nodes = [{"node": "node-a", "type": "node", "status": "online"}]
    plugin.resources = [
        resource("home-assistant", 100),
        resource("home-assistant", 200),
        resource("unique-service", 300, kind="lxc"),
        resource("unique-service", 301, template=1),
        resource("node-a", 400),
        resource("cloud-template", 500, template=1),
    ]

    plugin._get_nodes()
    assert plugin._guest_name_collisions == {"home-assistant", "node-a"}
    hosts = [
        plugin._handle_item("node-a", "qemu", resource("home-assistant", 100)),
        plugin._handle_item("node-a", "qemu", resource("home-assistant", 200)),
        plugin._handle_item("node-a", "lxc", resource("unique-service", 300, kind="lxc")),
        plugin._handle_item("node-a", "qemu", resource("node-a", 400)),
    ]
    assert hosts == ["home-assistant-100", "home-assistant-200", "unique-service", "node-a-400"]
    assert plugin.inventory.hosts["home-assistant-100"]["proxmox_name"] == "home-assistant"
    assert plugin.inventory.hosts["home-assistant-200"]["proxmox_name"] == "home-assistant"
    assert plugin.inventory.hosts["unique-service"]["proxmox_name"] == "unique-service"

    plugin.pools = [{"poolid": "app"}]
    plugin.pool_members = {
        "app": [
            {"name": "home-assistant", "vmid": 100},
            {"name": "home-assistant", "vmid": 200},
            {"name": "unique-service", "vmid": 300},
        ]
    }
    plugin._populate_pool_groups(hosts)
    assert plugin.inventory.groups["proxmox_pool_app"] == set(hosts[:3])

    conflict = plugin_type()
    conflict.nodes = plugin.nodes
    conflict.resources = [
        resource("home-assistant", 100),
        resource("home-assistant", 200),
        resource("home-assistant-100", 300),
    ]
    conflict._get_nodes()
    try:
        conflict._handle_item("node-a", "qemu", resource("home-assistant", 100))
    except AnsibleError as error:
        assert "unique inventory hostname" in str(error)
    else:
        raise AssertionError("colliding VMID-suffixed hostname was accepted")

    print("Proxmox inventory: duplicate guests stay distinct and pool groups retain VMID membership")


if __name__ == "__main__":
    main()
