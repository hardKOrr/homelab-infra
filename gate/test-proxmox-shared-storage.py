#!/usr/bin/env python3
"""Run the shared storage check against read-only pvesh fixtures.

Checks: runs the read-only shared storage tasks against fake pvesh responses; all-active,
absent, inactive and query-error cases check exit status, every node report, repeat output
and zero changes.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

REPO = Path(__file__).resolve().parents[1]
TASKS = REPO / 'ansible/tasks/proxmox/check-shared-storage.yml'
PVESH = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ['STORAGE_FIXTURE'])
args = sys.argv[1:]
with (root / 'calls').open('a') as log:
    log.write(' '.join(args) + '\\n')
assert args[0] == 'get' and args[2:] == ['--output-format', 'json'], args
path = args[1]
if path == '/nodes':
    print(json.dumps([{'node': 'localhost'}, {'node': 'peer'}]))
elif path == '/storage':
    print((root / 'definitions.json').read_text())
else:
    assert path in ['/nodes/localhost/storage', '/nodes/peer/storage'], args
    if os.environ['STORAGE_CASE'] == 'query-error' and path == '/nodes/peer/storage':
        sys.exit(1)
    print((root / ('peer.json' if path == '/nodes/peer/storage' else 'localhost.json')).read_text())
'''
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    binary = root / 'pvesh'
    binary.write_text(PVESH)
    binary.chmod(0o700)
    definitions = [
        {'storage': 'shared-iscsi', 'type': 'zfs', 'shared': 1, 'nodes': 'localhost'},
        {'storage': 'shared-nfs', 'type': 'nfs', 'shared': 1},
        {'storage': 'local', 'type': 'dir', 'shared': 0},
        {'storage': 'local-zfs', 'type': 'zfspool'},
    ]
    (root / 'definitions.json').write_text(json.dumps(definitions))
    active = [{'storage': name, 'active': 1} for name in ['shared-iscsi', 'shared-nfs']]
    (root / 'localhost.json').write_text(json.dumps(active))
    play = root / 'play.yml'
    play.write_text(yaml.safe_dump([{
        'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
        'vars': {'homelabinfra_config': {'proxmox': {
            'node': 'localhost', 'nodes': {}, 'api_host': 'localhost'}}},
        'tasks': [{'ansible.builtin.include_tasks': str(TASKS)}],
    }]))
    for case in ['all-active', 'one-missing', 'inactive', 'query-error']:
        peer = active if case != 'one-missing' else active[:1]
        if case == 'inactive':
            peer = [active[0], {'storage': 'shared-nfs', 'active': 0}]
        (root / 'peer.json').write_text(json.dumps(peer))
        env = dict(os.environ, STORAGE_FIXTURE=str(root), STORAGE_CASE=case,
                   PATH=str(root) + os.pathsep + os.environ['PATH'],
                   ANSIBLE_CONFIG=str(REPO / 'ansible/ansible.cfg'), ANSIBLE_NOCOLOR='1')
        outputs = []
        for _ in range(2):
            (root / 'calls').write_text('')
            result = subprocess.run([
                str(Path.home() / '.venvs/homelab-ansible/bin/ansible-playbook'),
                '-i', 'localhost,', str(play)], env=env, capture_output=True, text=True, timeout=90)
            assert (result.returncode == 0) == (case == 'all-active'), (case, result.stdout, result.stderr)
            assert 'changed=0' in result.stdout, result.stdout
            report = ' '.join(result.stdout.split())
            assert 'localhost: shared-iscsi=active, shared-nfs=active' in report, result.stdout
            state = 'active' if case == 'all-active' else 'missing'
            iscsi = 'missing' if case == 'query-error' else 'active'
            assert f'peer: shared-iscsi={iscsi}, shared-nfs={state}' in report, result.stdout
            assert (root / 'calls').read_text().splitlines() == [
                'get /storage --output-format json', 'get /nodes --output-format json',
                'get /nodes/localhost/storage --output-format json',
                'get /nodes/peer/storage --output-format json']
            outputs.append([line for line in result.stdout.splitlines() if 'shared-iscsi=' in line])
        assert outputs[0] == outputs[1], outputs
        print(f'{case}: PASS (repeat identical, changed=0)')
