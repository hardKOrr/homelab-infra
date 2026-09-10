"""Validation shared by config-doctor and the device passthrough contract gate."""

DEVICE_KINDS = {"igpu", "gpu", "usb", "other"}
DEVICE_MODES = {"shared", "dedicated"}


def validate_device_modes(proxmox, report):
    """Report malformed device declarations and mixed iGPU modes across nodes.

    ``report`` accepts ``(key, message)``.  A declaration without ``kind`` defaults to
    ``igpu`` only for ``mode: shared``; a bare ``mode: dedicated`` declaration is the
    pre-existing dedicated-only GPU shape from #130 and does not opt into this iGPU rule.
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
            # declaration remains the #130 dedicated-only GPU shape so this rule does not
            # reject Ollama/ComfyUI merely because they share a cluster with an iGPU.
            kind = "igpu" if mode == "shared" else "gpu"
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
    # only the cross-node failover invariant requested by #157. Shared is the reference mode
    # named by the contract, so each dedicated declaration that conflicts with it is reported
    # once rather than once per conflicting pair.
    reference_mode = "shared"
    for name, node, mode in igpu_declarations:
        if mode != "dedicated":
            continue
        reference = next(
            (
                declaration
                for declaration in igpu_declarations
                if declaration[1] != node and declaration[2] == reference_mode
            ),
            None,
        )
        if reference is None:
            continue
        report(
            "proxmox.devices.%s.mode" % name,
            "iGPU mode %r on node %s conflicts with mode %r on node %s; "
            "all iGPU-mode nodes must use one mode so shared consumers can fail over "
            "without contending with a dedicated VM"
            % (mode, node, reference[2], reference[1]),
        )
