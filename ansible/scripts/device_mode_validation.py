"""Validation shared by config-doctor and the device passthrough contract gate."""

from itertools import combinations


DEVICE_KINDS = {"igpu", "gpu", "usb", "other"}
DEVICE_MODES = {"shared", "dedicated"}


def validate_device_modes(proxmox, report):
    """Report malformed device declarations and mixed iGPU modes across nodes.

    ``report`` accepts ``(key, message)``.  A declaration without ``kind`` deliberately
    defaults to ``igpu``: the #130 shape remains covered by this cross-node guard instead of
    silently opting out.  Dedicated-only GPU and USB declarations can opt out explicitly.
    """
    if not isinstance(proxmox, dict):
        return

    devices = proxmox.get("devices")
    if devices in (None, {}):
        return
    if not isinstance(devices, dict):
        report("proxmox.devices", "must be a mapping of physical-device declarations")
        return

    target_node = proxmox.get("node")
    igpu_declarations = []

    for name, declaration in devices.items():
        key = "proxmox.devices.%s" % name
        if not isinstance(declaration, dict):
            report(key, "must be a mapping with mode and identifiers")
            continue

        mode = declaration.get("mode")
        if mode not in DEVICE_MODES:
            report("%s.mode" % key,
                   "%r is not recognised -- one of shared | dedicated" % mode)

        identifiers = declaration.get("identifiers")
        if not isinstance(identifiers, list) or not identifiers:
            report("%s.identifiers" % key, "must be a non-empty list")
        elif any(not isinstance(identifier, str) or not identifier
                 for identifier in identifiers):
            report("%s.identifiers" % key, "every identifier must be a non-empty string")

        kind = declaration.get("kind", "igpu")
        if kind not in DEVICE_KINDS:
            report("%s.kind" % key,
                   "%r is not recognised -- one of igpu | gpu | usb | other" % kind)

        node = declaration.get("node", target_node)
        if node in (None, ""):
            # proxmox.node has its own required-key report. Avoid making an absent fallback
            # produce a second, less useful device declaration error here.
            continue
        if not isinstance(node, str):
            report("%s.node" % key, "must be a node name")
            continue

        if mode in DEVICE_MODES and kind == "igpu":
            igpu_declarations.append((name, node, mode))

    # This intentionally compares only declarations on DIFFERENT nodes. The existing #130
    # per-device mutual-exclusivity check remains responsible for one node; this guard adds
    # only the cross-node failover invariant requested by #157.
    for left, right in combinations(igpu_declarations, 2):
        if left[1] == right[1] or left[2] == right[2]:
            continue
        report(
            "proxmox.devices.%s.mode" % right[0],
            "iGPU mode %r on node %s conflicts with mode %r on node %s; "
            "all iGPU-mode nodes must use one mode so shared consumers can fail over "
            "without contending with a dedicated VM"
            % (right[2], right[1], left[2], left[1]),
        )
        break
