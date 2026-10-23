#!/usr/bin/env python3
"""List configured, active, and pending references to a PBS datastore volume."""

import argparse
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("volid")
    parser.add_argument("--config-dir", default="/etc/pve")
    args = parser.parse_args()
    references = set()
    root = Path(args.config_dir)
    for guest_type in ("qemu-server", "lxc"):
        for config in sorted((root / "nodes").glob(f"*/{guest_type}/*.conf")):
            section = None
            vmid = config.stem
            for line in config.read_text(encoding="utf-8").splitlines():
                section_match = re.match(r"^\[([^]]+)\]$", line)
                if section_match:
                    section = section_match.group(1)
                    continue
                # The base, [current], and [PENDING] sections can all describe a
                # configured or soon-to-be-active device. Named snapshot sections are
                # historical and do not represent another concurrent guest writer.
                if section not in (None, "current", "PENDING"):
                    continue
                match = re.match(
                    r"^\s*((?:scsi|sata|ide|virtio|unused|mp|rootfs|efidisk|tpmstate)\d*):\s*([^,\s]+)",
                    line,
                )
                if match and match.group(2) == args.volid:
                    references.add((guest_type, vmid, match.group(1)))
    print(
        json.dumps(
            [
                {"guest_type": guest_type, "vmid": vmid, "device": device}
                for guest_type, vmid, device in sorted(
                    references,
                    key=lambda item: (item[0] != "qemu-server", item[1], item[2]),
                )
            ],
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
