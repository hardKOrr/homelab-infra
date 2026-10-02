#!/usr/bin/env python3
"""Exercise the real bootstrap pct-create command against a recording stub."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
script = (root / 'rundeck/bootstrap-rundeck.sh').read_text()
match = re.search(r'^  pct create .*?(?=^fi\s*$)', script, re.M | re.S)
assert match, 'runner creation block missing'
command = match.group(0)
with tempfile.TemporaryDirectory() as temp:
    directory = Path(temp)
    stub = directory / 'pct'
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    stub.chmod(0o755)
    for override, lab_dns, expected in (
        ('', '', '172.20.30.1'),
        ('', '172.20.30.53', '172.20.30.53'),
        ('172.20.30.54', '172.20.30.53', '172.20.30.54'),
        ('172.20.30.53 172.20.30.54', '', '172.20.30.53 172.20.30.54'),
    ):
        env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'],
            CAPTURE=str(directory / 'args'), CT_DNS=override, LAB_NET_DNS=lab_dns,
            CT_GW='172.20.30.1', VMID='1234', TEMPLATE_STORAGE='shared-content',
            TEMPLATE='debian-fixture.tar.zst', CT_HOSTNAME='runner-fixture',
            CT_CORES='2', CT_MEMORY='4096', CT_SWAP='512', CT_STORAGE='shared-root',
            CT_DISK='16', CT_BRIDGE='vmbr0', CT_IP='172.20.30.100/24',
            CT_VLAN_TAG=',tag=30', MANAGED_TAGS='_+lab;_-debian;_rundeck')
        subprocess.run(['bash', '-euc', command], env=env, check=True)
        recorded = (directory / 'args').read_text().splitlines()
        assert recorded[recorded.index('--nameserver') + 1] == expected
        assert recorded[recorded.index('--net0') + 1].endswith('tag=30')
print('runner DNS bootstrap: 4 resolver precedence/quoting cases passed')
