"""Keep the pinned Proxmox parser, with explicit trust and completion boundaries."""
from functools import partial

from ansible.errors import AnsibleError
from ansible_collections.community.proxmox.plugins.inventory.proxmox import (
    DOCUMENTATION as UPSTREAM_DOCUMENTATION,
    InventoryModule as ProxmoxInventory,
)

DOCUMENTATION = UPSTREAM_DOCUMENTATION.replace(
    "name: proxmox", "name: homelab_proxmox"
).replace("community.proxmox.proxmox", "homelab_proxmox")


class InventoryModule(ProxmoxInventory):
    NAME = "homelab_proxmox"

    def _get_session(self):
        if self.session is None:
            session = super()._get_session()
            # Requests applies CA environment settings to verify=None before merging
            # Session.verify. Bind the declared policy at the request boundary instead.
            # True still consumes the declared CA environment; False stays independent.
            session.request = partial(session.request, verify=session.verify)
            session.hooks["response"].append(self._validate_response)
        return self.session

    @staticmethod
    def _validate_response(response, **kwargs):
        if response.status_code < 400:
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), (list, dict)):
                raise AnsibleError("Proxmox inventory received an invalid data envelope")
        return response

    def _get_lxc_per_node(self, node):
        return self._guest_list(super()._get_lxc_per_node(node))

    def _get_qemu_per_node(self, node):
        return self._guest_list(super()._get_qemu_per_node(node))

    @staticmethod
    def _guest_list(guests):
        if not isinstance(guests, list) or any(
            not isinstance(guest, dict)
            or not all(key in guest for key in ("name", "vmid", "status"))
            for guest in guests
        ):
            raise AnsibleError("Proxmox inventory received an invalid guest list")
        return guests

    def _get_nodes(self):
        nodes = super()._get_nodes()
        if not isinstance(nodes, list) or any(
            not isinstance(node, dict)
            or not node.get("node")
            or node.get("type") != "node"
            or node.get("status") not in ("online", "offline")
            for node in nodes
        ):
            raise AnsibleError("Proxmox inventory received an invalid node list")
        self._offline_nodes = sorted(node["node"] for node in nodes if node["status"] == "offline")
        return nodes

    def parse(self, inventory, loader, path, cache=True):
        # A partial parse, or a failed refresh after a successful parse, is unavailable.
        inventory.set_variable("all", "homelabinfra_proxmox_inventory_complete", False)
        inventory.set_variable("all", "homelabinfra_proxmox_inventory_offline_nodes", [])
        self._offline_nodes = []
        super().parse(inventory, loader, path, cache=cache)
        # The upstream parser retains offline nodes but cannot enumerate their guests.
        # Diagnostics may use that partial view; selection/allocation must refuse it.
        inventory.set_variable("all", "homelabinfra_proxmox_inventory_offline_nodes", self._offline_nodes)
        inventory.set_variable("all", "homelabinfra_proxmox_inventory_complete", not self._offline_nodes)
