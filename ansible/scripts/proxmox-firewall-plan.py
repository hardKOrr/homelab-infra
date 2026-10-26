#!/usr/bin/env python3
"""Render and compare the configured Proxmox host-firewall baseline."""

import ipaddress
import json
import re
import sys


BASE_COMMENT = "homelab-infra: base "
RULE_MATCH_FIELDS = ("dest", "iface", "macro", "proto", "dport", "source")


def _fail(message):
    raise ValueError(message)


def _cidr_list(value, key, *, required=False):
    if not isinstance(value, list):
        _fail(f"proxmox.firewall.{key} must be a list of CIDRs")
    if required and not value:
        _fail(f"proxmox.firewall.{key} must contain at least one CIDR")
    result = []
    for cidr in value:
        if not isinstance(cidr, str) or not cidr.strip():
            _fail(f"proxmox.firewall.{key} entries must be non-empty CIDRs")
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            _fail(f"proxmox.firewall.{key} contains an invalid CIDR")
        if cidr not in result:
            result.append(cidr)
    return result


def _ip(value, key):
    if not isinstance(value, str) or not value.strip():
        _fail(f"{key} must be an IP address")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        _fail(f"{key} must be an IP address")
    return value


def _node_name(value, key):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        _fail(f"{key} must be a Proxmox node name")
    return value


def _normalize(value):
    return str(value).strip().lower()


def _rule_matches(live, wanted):
    if any(key not in wanted and str(live.get(key) or "").strip()
           for key in RULE_MATCH_FIELDS):
        return False
    for key, value in wanted.items():
        actual = live.get(key)
        if key == "enable":
            try:
                if int(actual) != int(value):
                    return False
            except (TypeError, ValueError):
                return False
        elif key == "action":
            if _normalize(actual) != _normalize(value):
                return False
        elif key in ("type", "proto"):
            if _normalize(actual) != _normalize(value):
                return False
        elif key == "dport":
            if str(actual or "") != str(value):
                return False
        elif actual != value:
            return False
    return True


def _source_is_client_host(source, cidrs):
    try:
        address = ipaddress.ip_address(str(source))
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
            if network.prefixlen == network.max_prefixlen and address == network.network_address:
                return True
        except ValueError:
            continue
    return False


def _legacy_rule(rule, node, management_sources, node_addresses, storage_clients,
                 storage_nodes):
    """Recognize only the exact active rule shapes emitted by host-firewall.py."""
    if str(rule.get("comment", "") or "").strip():
        return False
    if (_normalize(rule.get("type")) != "in"
            or _normalize(rule.get("action")) != "accept"):
        return False
    try:
        enabled = int(rule.get("enable", 1)) == 1
    except (TypeError, ValueError):
        enabled = False
    if not enabled:
        return False

    source = str(rule.get("source", "") or "")
    proto = _normalize(rule.get("proto"))
    dport = str(rule.get("dport", "") or "")
    if source in management_sources:
        if proto == "tcp" and dport in ("22,8006,3128", "22,8006"):
            return True
        if proto in ("icmp", "icmpv6", "ipv6-icmp") and not dport:
            return True

    peer_source = source in node_addresses.values()
    if peer_source:
        if proto == "tcp" and dport == "60000:60050":
            return True
        if proto == "udp" and dport == "5405:5412":
            return True
        if proto in ("icmp", "icmpv6", "ipv6-icmp") and not dport:
            return True

    if (node in storage_nodes and _source_is_client_host(source, storage_clients)
            and proto == "tcp" and dport == "22,111,2049,3260"):
        return True
    return False


def _plan_rules(current, desired, *, node, management_sources, node_addresses,
                storage_clients, storage_nodes):
    if not isinstance(current, list) or any(not isinstance(rule, dict) for rule in current):
        _fail("Proxmox firewall rules must be a list of rule mappings")
    keep = set()
    create = []
    for wanted in desired:
        exact = [index for index, live in enumerate(current) if _rule_matches(live, wanted)]
        if exact:
            keep.add(exact[0])
        else:
            create.append(wanted)

    delete = []
    for index, live in enumerate(current):
        comment = str(live.get("comment", "") or "")
        owned_base = comment.startswith(BASE_COMMENT)
        legacy = _legacy_rule(live, node, management_sources, node_addresses,
                              storage_clients, storage_nodes)
        if index in keep or not (owned_base or legacy):
            continue
        if "pos" not in live:
            _fail("a Proxmox firewall rule selected for removal has no position")
        delete.append(live)
    delete.sort(key=lambda rule: int(rule["pos"]), reverse=True)
    return {"create": create, "delete": delete}


def _option_matches(current, wanted):
    for key, value in wanted.items():
        actual = current.get(key)
        if key == "enable":
            try:
                if int(actual) != value:
                    return False
            except (TypeError, ValueError):
                return False
        elif _normalize(actual) != _normalize(value):
            return False
    return True


def plan(data):
    if not isinstance(data, dict):
        _fail("planner input must be a mapping")
    proxmox = data.get("proxmox") or {}
    if not isinstance(proxmox, dict):
        _fail("proxmox must be a mapping")
    firewall = proxmox.get("firewall") or {}
    if not isinstance(firewall, dict):
        _fail("proxmox.firewall must be a mapping")
    enabled = firewall.get("enabled", False)
    if not isinstance(enabled, bool):
        _fail("proxmox.firewall.enabled must be a boolean")
    if not enabled:
        return {
            "enabled": False,
            "changed": False,
            "cluster": {"desired": [], "create": [], "delete": [], "options_changed": False},
            "nodes": {},
            "members": [],
            "compute_nodes": [],
            "node_addresses": {},
        }

    management_sources = _cidr_list(
        firewall.get("management_sources", []), "management_sources", required=True)
    storage_clients = _cidr_list(firewall.get("storage_clients", []), "storage_clients")
    node = _node_name(proxmox.get("node"), "proxmox.node")

    node_addresses = proxmox.get("nodes") or {}
    if not isinstance(node_addresses, dict):
        _fail("proxmox.nodes must map node names to IP addresses")
    node_addresses = {
        _node_name(name, "proxmox.nodes key"): _ip(address, "proxmox.nodes value")
        for name, address in node_addresses.items()
    }
    if node not in node_addresses:
        node_addresses[node] = _ip(proxmox.get("api_host"), "proxmox.api_host")
    if len(set(node_addresses.values())) != len(node_addresses):
        _fail("proxmox.nodes must not assign one address to multiple nodes")

    if proxmox.get("storage_nodes") is not None:
        storage_nodes = proxmox.get("storage_nodes")
        if not isinstance(storage_nodes, list):
            _fail("proxmox.storage_nodes must be a list of node names")
    else:
        node_roles = proxmox.get("node_roles") or {}
        if not isinstance(node_roles, dict):
            _fail("proxmox.node_roles must be a mapping")
        role_storage = node_roles.get("storage")
        storage_nodes = [role_storage or node]
    storage_nodes = [_node_name(item, "proxmox.storage_nodes entry")
                     for item in storage_nodes]
    if len(set(storage_nodes)) != len(storage_nodes):
        _fail("proxmox.storage_nodes must not repeat a node")
    unknown_storage_nodes = set(storage_nodes) - set(node_addresses)
    if unknown_storage_nodes:
        _fail("proxmox.storage_nodes must be in proxmox.nodes")

    status = data.get("cluster_status") or []
    if not isinstance(status, list):
        _fail("Proxmox cluster status must be a list")
    members = []
    offline = []
    for item in status:
        if item.get("type") != "node":
            continue
        name = item.get("name") or item.get("node")
        if not name:
            _fail("Proxmox cluster status returned a node without a name")
        name = _node_name(name, "Proxmox cluster node")
        members.append(name)
        if str(item.get("online", "1")) not in ("1", "True", "true"):
            offline.append(name)
    if not members:
        _fail("Proxmox cluster status contains no nodes")
    if node not in members:
        _fail("proxmox.node is not a member of the current cluster")
    missing = set(members) - set(node_addresses)
    if missing:
        _fail("proxmox.nodes must include every current cluster node")
    if offline:
        _fail("all current Proxmox nodes must be online before firewall reconciliation")
    members = sorted(set(members))
    compute_nodes = sorted(set(members) - set(storage_nodes))

    desired_cluster = []
    for index, source in enumerate(management_sources, start=1):
        desired_cluster.extend([
            {"type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "22",
             "source": source, "enable": 1,
             "comment": f"{BASE_COMMENT}management-ssh-{index}"},
            {"type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "8006",
             "source": source, "enable": 1,
             "comment": f"{BASE_COMMENT}management-api-{index}"},
        ])

    peers = sorted(node_addresses.items())
    for source_name, source_address in peers:
        for destination_name, destination_address in peers:
            if source_name == destination_name:
                continue
            desired_cluster.append({
                "type": "in", "action": "ACCEPT", "source": source_address,
                "dest": destination_address, "enable": 1,
                "comment": (f"{BASE_COMMENT}cluster-{source_name}-to-"
                            f"{destination_name}"),
            })

    desired_nodes = {}
    for storage_node in sorted(set(storage_nodes) & set(members)):
        desired_nodes[storage_node] = [
            {"type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "111,2049",
             "source": source, "enable": 1,
             "comment": f"{BASE_COMMENT}storage-nfs-{storage_node}-{index}"}
            for index, source in enumerate(storage_clients, start=1)
        ]

    cluster_rules = _plan_rules(
        data.get("cluster_rules", []), desired_cluster, node="",
        management_sources=management_sources, node_addresses=node_addresses,
        storage_clients=storage_clients, storage_nodes=storage_nodes)
    cluster_options = data.get("cluster_options") or {}
    if not isinstance(cluster_options, dict):
        _fail("Proxmox datacenter options must be a mapping")
    desired_cluster_options = {"enable": 1, "policy_in": "DROP", "policy_out": "ACCEPT"}
    cluster_options_changed = not _option_matches(cluster_options, desired_cluster_options)

    current_node_rules = data.get("node_rules") or {}
    current_node_options = data.get("node_options") or {}
    if not isinstance(current_node_rules, dict) or not isinstance(current_node_options, dict):
        _fail("Proxmox node firewall state must be a mapping")
    node_plans = {}
    node_options_changed = []
    desired_node_options = {}
    for current_node in members:
        node_desired = desired_nodes.get(current_node, [])
        if not isinstance(current_node_options.get(current_node, {}), dict):
            _fail("Proxmox node firewall options must be mappings")
        node_plans[current_node] = _plan_rules(
            current_node_rules.get(current_node, []), node_desired,
            node=current_node, management_sources=management_sources,
            node_addresses=node_addresses, storage_clients=storage_clients,
            storage_nodes=storage_nodes)
        desired_node_options[current_node] = {"enable": 1}
        if not _option_matches(current_node_options.get(current_node, {}), {"enable": 1}):
            node_options_changed.append(current_node)

    changed = bool(
        cluster_rules["create"] or cluster_rules["delete"] or cluster_options_changed
        or node_options_changed
        or any(item["create"] or item["delete"] for item in node_plans.values())
    )
    return {
        "enabled": True,
        "changed": changed,
        "cluster": {
            **cluster_rules,
            "desired": desired_cluster,
            "options_changed": cluster_options_changed,
            "desired_options": desired_cluster_options,
        },
        "nodes": {
            name: {
                **node_plans[name],
                "desired": desired_nodes.get(name, []),
                "options_changed": name in node_options_changed,
                "desired_options": desired_node_options[name],
            }
            for name in members
        },
        "members": members,
        "compute_nodes": compute_nodes,
        "node_addresses": node_addresses,
    }


def main():
    try:
        payload = json.load(sys.stdin)
        print(json.dumps(plan(payload), sort_keys=True))
    except (ValueError, TypeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
