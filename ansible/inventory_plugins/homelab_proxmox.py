"""Keep the pinned Proxmox parser, with explicit trust and completion boundaries."""
from collections import Counter
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
            or node.get("status") not in ("online", "offline", "unknown")
            for node in nodes
        ):
            raise AnsibleError("Proxmox inventory received an invalid node list")
        self._offline_nodes = sorted(node["node"] for node in nodes if node["status"] != "online")

        # The upstream parser uses the Proxmox name as the Ansible inventory key. Two
        # guests with the same name therefore overwrite one another before selectors can
        # notice the ambiguity. Read cluster resources once and suffix only names that
        # collide with another guest or a node; unique guest names remain unchanged.
        resources = self._get_json(f"{self.proxmox_url}/api2/json/cluster/resources?type=vm")
        if not isinstance(resources, list) or any(
            not isinstance(resource, dict)
            or resource.get("type") not in ("lxc", "qemu")
            or not isinstance(resource.get("name"), str)
            or not resource.get("name")
            or "vmid" not in resource
            for resource in resources
        ):
            raise AnsibleError("Proxmox inventory received an invalid cluster resource list")
        guests = [resource for resource in resources if not bool(resource.get("template", 0))]
        name_counts = Counter(guest["name"] for guest in guests)
        node_names = {node["node"] for node in nodes}
        self._guest_name_collisions = {
            name for name, count in name_counts.items() if count > 1 or name in node_names
        }
        self._inventory_hosts_by_vmid = {}
        self._inventory_name_conflicts = node_names | {guest["name"] for guest in guests}
        self._assigned_inventory_guest_names = set()
        # The pinned parser skips guest/IP probes only for offline nodes. Unknown
        # cluster status has the same incomplete coverage, so use that skip path.
        return [dict(node, status="offline") if node["status"] == "unknown" else node for node in nodes]

    def _handle_item(self, node, ittype, item):
        original_name = item["name"]
        inventory_item = item
        if original_name in self._guest_name_collisions:
            inventory_name = f"{original_name}-{item['vmid']}"
            if (
                inventory_name in self._inventory_name_conflicts
                or inventory_name in self._assigned_inventory_guest_names
            ):
                raise AnsibleError(
                    "Proxmox guest name and VMID do not produce a unique inventory hostname"
                )
            inventory_item = dict(item, name=inventory_name)

        host = super()._handle_item(node, ittype, inventory_item)
        if host is not None:
            self._inventory_hosts_by_vmid[str(item["vmid"])] = host
            self._assigned_inventory_guest_names.add(host)
            if host != original_name:
                # The inventory key is disambiguated; retain the actual Proxmox name as
                # a fact so playbooks continue to see the guest's real name.
                self.inventory.set_variable(host, self._fact("name"), original_name)
        return host

    def _populate_pool_groups(self, added_hosts):
        # Pool membership includes VMIDs, which lets duplicate-named guests remain
        # distinct while preserving the upstream pool groups.
        for pool in self._get_pools():
            poolid = pool.get("poolid")
            if not poolid:
                continue
            pool_group = self._group(f"pool_{poolid}")
            self.inventory.add_group(pool_group)

            for member in self._get_members_per_pool(poolid):
                vmid = member.get("vmid")
                host = (
                    self._inventory_hosts_by_vmid.get(str(vmid))
                    if vmid is not None
                    else None
                )
                if host is None:
                    name = member.get("name")
                    host = name if name in added_hosts else None
                if host in added_hosts:
                    self.inventory.add_child(pool_group, host)

    def parse(self, inventory, loader, path, cache=True):
        # A partial parse, or a failed refresh after a successful parse, is unavailable.
        inventory.set_variable("all", "homelabinfra_proxmox_inventory_complete", False)
        inventory.set_variable("all", "homelabinfra_proxmox_inventory_offline_nodes", [])
        self._offline_nodes = []
        self._guest_name_collisions = set()
        self._inventory_hosts_by_vmid = {}
        self._inventory_name_conflicts = set()
        self._assigned_inventory_guest_names = set()
        super().parse(inventory, loader, path, cache=cache)
        # The upstream parser retains offline/unknown nodes but cannot enumerate their guests.
        # Diagnostics may use that partial view; selection/allocation must refuse it.
        inventory.set_variable("all", "homelabinfra_proxmox_inventory_offline_nodes", self._offline_nodes)
        inventory.set_variable("all", "homelabinfra_proxmox_inventory_complete", not self._offline_nodes)
