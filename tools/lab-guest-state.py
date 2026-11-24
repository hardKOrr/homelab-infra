#!/usr/bin/env python3
"""Read-only node-side guest state for lab-drill's removal isolation check.

Called on a Proxmox node via lab-exec --node, not directly on the workstation.
Private identities and configurations are returned only to the drill's private log
directory. Runtime counters are excluded; owned guests' configuration and power
state are retained. Does not change any guest.
"""
import json
from pathlib import Path
import subprocess

resources = json.loads(subprocess.check_output(
    ['pvesh', 'get', '/cluster/resources', '--type', 'vm', '--output-format', 'json']))
state = {}
for guest in resources:
    if '_+lab' not in (guest.get('tags') or '').split(';'):
        continue
    kind = 'qemu-server' if guest['type'] == 'qemu' else 'lxc'
    path = Path('/etc/pve/nodes') / guest['node'] / kind / f"{guest['vmid']}.conf"
    state[str(guest['vmid'])] = dict(name=guest.get('name'), status=guest['status'],
                                    config=path.read_text())
print(json.dumps(state, sort_keys=True))
