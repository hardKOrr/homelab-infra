#!/usr/bin/env python3
"""List exact QEMU disk references to a PBS datastore volume."""

import argparse
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("volid")
    parser.add_argument("--config-dir", default="/etc/pve")
    args = parser.parse_args()
    references = []
    root = Path(args.config_dir)
    for guest_type in ("qemu-server", "lxc"):
        for config in sorted((root / "nodes").glob(f"*/{guest_type}/*.conf")):
            vmid = config.stem
            in_snapshot = False
            for line in config.read_text(encoding="utf-8").splitlines():
                if re.match(r"^\s*\[[^]]+\]\s*$", line):
                    in_snapshot = True
                    continue
                if in_snapshot:
                    continue
                match = re.match(
                    r"^\s*((?:scsi|sata|ide|virtio|unused|mp|rootfs|efidisk|tpmstate)\d*):\s*([^,\s]+)",
                    line,
                )
                if match and match.group(2) == args.volid:
                    references.append(
                        {"vmid": vmid, "device": match.group(1), "guest_type": guest_type}
                    )
    print(json.dumps(references, sort_keys=True))


if __name__ == "__main__":
    main()
