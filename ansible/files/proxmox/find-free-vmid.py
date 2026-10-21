#!/usr/bin/env python3
"""Choose a free Proxmox VMID while keeping a reserved PBS volume owner unused."""

import argparse
import json
import subprocess


MIN_VMID = 100
MAX_VMID = 999_999_999


def first_free_vmid(resources, start, reserved):
    """Return the first unused VMID at or after start, wrapping at Proxmox's limit."""
    used = {int(resource["vmid"]) for resource in resources}
    used.add(int(reserved))

    span = MAX_VMID - MIN_VMID + 1
    candidate = MIN_VMID + (int(start) - MIN_VMID) % span
    initial = candidate
    while candidate in used:
        candidate += 1
        if candidate > MAX_VMID:
            candidate = MIN_VMID
        if candidate == initial:
            raise RuntimeError("no free Proxmox VMID is available")
    return candidate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start", type=int)
    parser.add_argument("reserved", type=int)
    args = parser.parse_args()

    result = subprocess.run(
        ["pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    print(first_free_vmid(json.loads(result.stdout), args.start, args.reserved))


if __name__ == "__main__":
    main()
