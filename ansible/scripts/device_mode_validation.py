"""Validation shared by config-doctor and the device passthrough contract gate."""

import re

DEVICE_KINDS = {"igpu", "gpu", "usb", "other"}
DEVICE_MODES = {"shared", "dedicated"}


def validate_device_modes(proxmox, report):
    """Report malformed devices and unsafe shared iGPU failover declarations.

    ``report`` accepts ``(key, message)``.  A declaration without ``kind`` defaults to
    ``igpu`` only for ``mode: shared``; a bare ``mode: dedicated`` declaration is the
    pre-existing dedicated-only GPU shape and does not opt into this iGPU rule.
    A dedicated declaration for an iGPU must say ``kind: igpu`` explicitly.
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

        kind = declaration.get("kind")
        if kind is None:
            # Shared declarations are iGPU-mode declarations by default. A bare dedicated
            # declaration remains the dedicated-only GPU shape so this rule does not
            # reject Ollama/ComfyUI merely because they share a cluster with an iGPU.
            kind = "igpu" if mode == "shared" else "gpu"
        if kind not in DEVICE_KINDS:
            report("%s.kind" % key,
                   "%r is not recognised -- one of igpu | gpu | usb | other" % kind)

        if mode == "dedicated" and kind in {"igpu", "gpu"}:
            if "rombar" in declaration and not isinstance(declaration["rombar"], bool):
                report("%s.rombar" % key, "must be a boolean")
            if "legacy_igd" in declaration:
                if not isinstance(declaration["legacy_igd"], bool):
                    report("%s.legacy_igd" % key, "must be a boolean")
                elif declaration["legacy_igd"] and (kind != "igpu" or declaration.get("rombar") is False):
                    report("%s.legacy_igd" % key, "requires kind igpu and an enabled ROM BAR")
            device_node = declaration.get("node")
            if not isinstance(device_node, str) or not device_node.strip():
                report("%s.node" % key,
                       "a dedicated GPU declaration must name the node that owns the PCI device")
            pci_ids = [identifier for identifier in identifiers or []
                       if isinstance(identifier, str)
                       and re.fullmatch(r"[0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]", identifier)]
            if len(pci_ids) != 1:
                report("%s.identifiers" % key,
                       "a dedicated GPU declaration must include exactly one PCI identifier")
            mapping_id = declaration.get("pci_mapping", name)
            if not isinstance(mapping_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", mapping_id):
                report("%s.pci_mapping" % key,
                       "must be a valid Proxmox PCI resource mapping ID")

        node = declaration.get("node", target_node)
        if node in (None, ""):
            # proxmox.node has its own required-key report. Avoid making an absent fallback
            # produce a second, less useful device declaration error here.
            continue
        if not isinstance(node, str):
            report("%s.node" % key, "must be a node name")
            continue

        if mode in DEVICE_MODES and kind == "igpu":
            igpu_declarations.append((name, node, mode, declaration.get("node")))

    # Shared failover must never reach a node whose iGPU belongs to a VM. Mixed
    # modes are safe only when every shared declaration explicitly restricts its
    # consumer to a different node; attachment and placement enforce that restriction.
    dedicated_nodes = {node for _, node, mode, _ in igpu_declarations if mode == "dedicated"}
    for name, node, mode, explicit_node in igpu_declarations:
        if mode != "shared" or not dedicated_nodes:
            continue
        if not explicit_node or node in dedicated_nodes:
            report(
                "proxmox.devices.%s.node" % name,
                "shared iGPU declarations alongside dedicated iGPUs must name an "
                "explicit node without a dedicated iGPU; refusing unsafe failover",
            )
