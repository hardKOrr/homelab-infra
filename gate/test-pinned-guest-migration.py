#!/usr/bin/env python3
"""Exercise live-config migration decisions and the shared execution boundary."""
import copy
import json
import os
import subprocess
import tempfile
import importlib.util
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('migration', ROOT / 'ansible/scripts/guest-migration-plan.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BASE = dict(source='node-a', target='node-b', type='qemu', vmid=204,
            config={'tags': '_+lab;_app', 'scsi0': 'shared:vm-204-disk-0',
                    'ide2': 'shared:vm-204-cloudinit,media=cdrom'},
            storages=[dict(storage='shared', shared=1, active=1, enabled=1)], ha=[])


def refusal(data, reason):
    try:
        module.plan(data)
    except ValueError as exc:
        assert reason in str(exc), str(exc)
    else:
        raise AssertionError(f'expected refusal: {reason}')


def execution_test():
    ansible = Path.home() / '.venvs/homelab-ansible/bin/ansible-playbook'
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'playbooks/apps').mkdir(parents=True)
        (root / 'scripts').mkdir()
        (root / 'scripts/guest-migration-plan.py').write_text(
            (ROOT / 'ansible/scripts/guest-migration-plan.py').read_text())
        stub = root / 'pvesh'
        stub.write_text('#!/usr/bin/env python3\n' + """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
state = Path(os.environ['MIGRATION_STATE'])
if args[0] == 'create':
    assert '--online' in args or '--restart' in args
    state.write_text('moved')
    value = 'UPID:node-a:123:456:789:migrate:204:root@pam:'
elif args[1].endswith('/config'):
    value = json.loads(os.environ['MIGRATION_CONFIG'])
elif args[1].endswith('/storage'):
    value = [{'storage': 'shared', 'shared': 1, 'active': 1}]
elif args[1] == '/cluster/ha/resources':
    value = ([{'sid': ('vm:' if os.environ['MIGRATION_TYPE'] == 'qemu' else 'ct:') + '204',\n               'comment': 'managed by homelab-infra', 'state': 'started'}]\n             if os.environ['MIGRATION_HA'] == '1' else [])
elif '/tasks/' in args[1]:
    value = {'status': 'stopped', 'exitstatus': 'OK'}
else:
    value = [{'vmid': 204, 'type': os.environ['MIGRATION_TYPE'],
              'node': 'node-b' if state.exists() else 'node-a', 'status': 'running'}]
print(json.dumps(value))
""")
        stub.chmod(0o755)
        ha_stub = root / 'ha-manager'
        ha_stub.write_text('#!/usr/bin/env python3\n'
                           'import os,sys\nfrom pathlib import Path\n'
                           'assert sys.argv[1] == "migrate"\n'
                           'Path(os.environ["MIGRATION_STATE"]).write_text("ha")\n')
        ha_stub.chmod(0o755)
        inventory = root / 'inventory'
        inventory.write_text('localhost ansible_connection=local\nnode-a ansible_connection=local\nnode-b ansible_connection=local\n')
        for kind in ('qemu', 'lxc'):
            for target, local, ha in (('node-b', False, False), ('', False, False),
                                      ('node-a', False, False), ('node-b', True, False),
                                      ('node-b', False, True)):
                state = root / 'state'
                state.unlink(missing_ok=True)
                config = {'tags': '_+lab', 'scsi0' if kind == 'qemu' else 'rootfs':
                          ('local' if local else 'shared') + ':disk'}
                book = root / 'playbooks/apps/test.yml'
                book.write_text(yaml.safe_dump([{
                    'hosts': 'localhost', 'gather_facts': False,
                    'environment': {'PATH': str(root) + ':' + os.environ['PATH'],
                                    'MIGRATION_STATE': str(state), 'MIGRATION_TYPE': kind,
                                    'MIGRATION_HA': '1' if ha else '0',
                                    'MIGRATION_CONFIG': json.dumps(config)},
                    'vars': {'homelabinfra_config': {'proxmox': {'node': 'node-a',
                              'nodes': {'node-a': '127.0.0.1', 'node-b': '127.0.0.1'}}},
                             'migrate_guest_id': 204, 'migrate_guest_type': kind,
                             'migrate_guest_source': 'node-a', 'migrate_guest_target': target},
                    'tasks': [{'ansible.builtin.include_tasks': str(ROOT / 'ansible/tasks/proxmox/migrate-pinned-guest.yml')},
                              {'ansible.builtin.assert': {'that':
                               "migrate_guest_node == '" + (target or 'node-a') + "'"}}]}]))
                result = subprocess.run([str(ansible), '-i', str(inventory), str(book)],
                                        capture_output=True, text=True,
                                        env=os.environ | {'ANSIBLE_STDOUT_CALLBACK': 'default', 'ANSIBLE_NOCOLOR': '1'})
                output = result.stdout + result.stderr
                if local:
                    assert result.returncode != 0 and 'node-local' in output, output
                    assert not state.exists()
                else:
                    assert result.returncode == 0, output
                    assert state.exists() == (target == 'node-b'), output
                    if state.exists():
                        assert state.read_text() == ('ha' if ha else 'moved'), output


def main():
    assert module.plan(BASE)['mode'] == 'qemu'
    lxc = copy.deepcopy(BASE)
    lxc.update(type='lxc', config={'tags': '_+lab', 'rootfs': 'shared:subvol-204-disk-0,size=8G'})
    assert module.plan(lxc)['mode'] == 'lxc'
    for value in ('', 'node-a'):
        assert module.plan(BASE | {'target': value}) == {'mode': 'keep', 'node': 'node-a'}
    assert module.plan(BASE | {'gpu': True})['mode'] == 'keep'
    for guest, sid in ((BASE, 'vm:204'), (lxc, 'ct:204')):
        ha = guest | {'ha': [dict(sid=sid, comment='managed by homelab-infra')]}
        assert module.plan(ha)['mode'] == 'ha'
        refusal(guest | {'ha': [dict(sid=sid)]}, 'HA resource')
    refusal(BASE | {'storages': [dict(storage='shared', shared=0, active=1)]}, 'node-local')
    refusal(BASE | {'config': BASE['config'] | {'ide1': 'cdrom,media=cdrom'}}, 'host device')
    refusal(BASE | {'storages': []}, 'unknown storage')
    refusal(BASE | {'storages': [dict(storage='shared', shared=1, active=0)]}, 'not active')
    for key, value in [('hostpci0', 'mapping=gpu'), ('usb0', 'host=1-2'),
                       ('virtiofs0', 'dirid=media'), ('dev0', '/dev/dri/renderD128'),
                       ('mp0', '/mnt/media,mp=/media'), ('args', '-device'),
                       ('hookscript', 'local:snippets/hook'), ('ivshmem', 'size=32'),
                       ('vmstate', 'local:state'), ('serial0', '/dev/ttyS0'),
                       ('parallel0', '/dev/parport0'), ('rng0', 'source=/dev/hwrng'),
                       ('lxc.mount.entry', '/dev/foo')]:
        refusal(BASE | {'config': BASE['config'] | {key: value}}, 'pins the guest')
    for disk in ('scsi1', 'unused0', 'efidisk0', 'tpmstate0', 'rootfs', 'mp1'):
        refusal(BASE | {'config': BASE['config'] | {disk: 'local:disk'}}, 'node-local')
    refusal(BASE | {'config': BASE['config'] | {'tags': '_+lab-other'}}, 'ownership')
    refusal(BASE | {'config': BASE['config'] | {'lock': 'backup'}}, 'locked')
    tasks = yaml.safe_load((ROOT / 'ansible/tasks/proxmox/migrate-pinned-guest.yml').read_text())
    block = tasks[1]['block']
    names = [t['name'] for t in block]
    assert names.index('Guest migration | Validate migration safety') < names.index('Guest migration | Submit migration through HA')
    direct = next(t for t in block if t['name'].endswith('Submit shared-storage migration'))
    assert "['--online', '1']" in direct['ansible.builtin.command']['argv']
    assert "['--restart', '1']" in direct['ansible.builtin.command']['argv']
    assert any(t.get('ansible.builtin.include_tasks') == 'wait-for-task.yml' for t in block)
    assert 'migrate_guest_target != migrate_guest_source' in tasks[1]['when']
    execution_test()
    print('Pinned guest migration: move, keep, HA and refusal paths pass')


if __name__ == '__main__':
    main()
