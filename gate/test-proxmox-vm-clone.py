#!/usr/bin/env python3
"""Exercise cloned-VM NIC/cloud-init updates and SSH refusal without lab access."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = Path.home() / '.ansible/collections'
sys.path.insert(0, str(COLLECTIONS))
from ansible_collections.community.proxmox.plugins.modules import proxmox_kvm as upstream
from ansible_collections.community.proxmox.plugins.module_utils.version import LooseVersion

init_plugin_loader()


def load(relative):
    return yaml.safe_load((ROOT / relative).read_text())


TASKS = load('ansible/tasks/proxmox/vm-clone.yml')


def task(name):
    return next(t for t in TASKS if t.get('name') == 'VM clone | ' + name)


def render(value, variables):
    return Templar(loader=DataLoader(), variables=variables).template(value)


def repository_args(address='198.51.100.50', vlan=317, existing=False, clone_changed=None):
    vm = deepcopy(load('ansible/vars/app-defaults/pbs.yml')['pbs_defaults']['proxmox']['vm'])
    vm.update(vmid=501, name='pbs-fixture')
    network = dict(ip_address=address, cidr='198.51.100.0/24', gateway='198.51.100.1',
                   bridge='fixturebr7', dns_servers=['192.0.2.53'], searchdomain=['example.test'])
    if vlan is not None:
        network['vlan'] = vlan
    variables = dict(
        omit='__fixture_omit__', _vmc_existing={'rc': 0 if existing else 2},
        _proxmox_startup='order=60', homelabinfra_instance={'network': network},
        homelabinfra_config={
            'proxmox': dict(node='fixture-node', api_host='192.0.2.10', api_user='fixture@pve',
                            api_token_id='fixture-id', api_token_secret='fixture-secret', vm=vm),
            'ansible': dict(ssh_user='fixture-admin', ssh_public_key='fixture-public-key'),
        },
    )
    variables[task('Clone the template')['register']] = {
        'changed': not existing if clone_changed is None else clone_changed,
    }
    for name in ('Set connection facts', 'Build network settings', 'Build the cloud-init IP configuration'):
        variables.update(render(task(name)['ansible.builtin.set_fact'], variables))
    args = render(task('Apply per-VM configuration')['community.proxmox.proxmox_kvm'], variables)
    return {k: v for k, v in args.items() if v != variables['omit']}, variables


class RecordingAPI:
    """Provider calls are replaced; pinned dispatch and update shaping remain real."""
    def __init__(self, writes, config, path=()):
        self.writes, self._config, self.path = writes, config, path

    def __getattr__(self, key):
        return RecordingAPI(self.writes, self._config, self.path + (key,))

    def __call__(self, key):
        return self.__getattr__(str(key))

    def set(self, **kwargs):
        self.writes.append((self.path, kwargs))
        self._config.update({k: v for k, v in kwargs.items() if v is not None})


def apply_update(args, config):
    params = {k: deepcopy(v.get('default')) for k, v in upstream.module_args().items()}
    params.update(args)
    writes = []

    class Finished(BaseException):
        pass

    def finish(**result):
        raise Finished(result)

    obj = upstream.ProxmoxKvmAnsible.__new__(upstream.ProxmoxKvmAnsible)
    obj.module = SimpleNamespace(params=params, exit_json=finish,
                                 fail_json=lambda **result: finish(failed=True, **result))
    obj.proxmox_api = RecordingAPI(writes, config)
    obj.version = lambda: LooseVersion('8.0')
    obj.get_node = lambda node: {'node': node}
    obj.get_vminfo = lambda *args, **kwargs: {}
    with patch.object(upstream, 'create_proxmox_module', return_value=obj.module), \
            patch.object(upstream, 'ProxmoxKvmAnsible', return_value=obj):
        try:
            upstream.main()
        except Finished as finished:
            result = finished.args[0]
            if result.get('failed') or not result.get('changed') or len(writes) != 1:
                raise AssertionError((result, writes))
            return writes[0]
    raise AssertionError('The pinned module did not return an Ansible result')


class VmCloneTests(unittest.TestCase):
    def template_config(self):
        build = next(t['block'] for t in load('ansible/tasks/proxmox/ensure-cloud-template.yml')
                     if t.get('name') == 'Cloud template | Build the template')
        shell = next(t for t in build if t.get('name') == 'Cloud template | Create the VM shell')
        argv = shell['ansible.builtin.command']['argv']
        nic = render(argv[argv.index('--net0') + 1], {'_ct_bridge': 'fixturebr7'})
        return dict(net0=nic, scsi0='fixture:vm-501-disk-0',
                    ide2='fixture:vm-501-cloudinit,media=cdrom')

    def test_original_update_drops_tagged_nic_but_applies_static_cloud_init(self):
        manifest = json.loads((COLLECTIONS / 'ansible_collections/community/proxmox/MANIFEST.json').read_text())
        self.assertEqual(manifest['collection_info']['version'], '2.0.0')
        args, _ = repository_args()
        args.pop('update_unsafe', None)  # The pre-fix repository call used the module default.
        config = self.template_config()
        inherited_nic = config['net0']
        path, payload = apply_update(args, config)
        self.assertEqual(path, ('nodes', 'fixture-node', 'qemu', '501', 'config'))
        self.assertNotIn('net0', payload)
        self.assertEqual(config['net0'], inherited_nic)
        self.assertNotIn('tag=', config['net0'])
        self.assertEqual(payload['ipconfig0'], 'ip=198.51.100.50/24,gw=198.51.100.1')
        print('REPRODUCED pinned 2.0.0: static cloud-init applied, declared tagged NIC dropped')

    def test_new_clones_apply_declared_network_before_boot(self):
        for address, vlan, nic, ipconfig in (
            ('198.51.100.50', 317, 'virtio,bridge=fixturebr7,tag=317',
             'ip=198.51.100.50/24,gw=198.51.100.1'),
            ('198.51.100.50', 0, 'virtio,bridge=fixturebr7',
             'ip=198.51.100.50/24,gw=198.51.100.1'),
            ('198.51.100.50', None, 'virtio,bridge=fixturebr7',
             'ip=198.51.100.50/24,gw=198.51.100.1'),
            ('dhcp', 317, 'virtio,bridge=fixturebr7,tag=317', 'ip=dhcp'),
        ):
            with self.subTest(address=address, vlan=vlan):
                args, _ = repository_args(address=address, vlan=vlan)
                config = self.template_config()
                disks = {k: config[k] for k in ('scsi0', 'ide2')}
                _, payload = apply_update(args, config)
                self.assertEqual(payload.get('net0'), nic)
                self.assertEqual(config['net0'], nic)
                self.assertEqual(payload['ipconfig0'], ipconfig)
                self.assertEqual(payload['ciuser'], 'fixture-admin')
                self.assertEqual(payload['sshkeys'], 'fixture-public-key')
                self.assertEqual(payload['nameserver'], '192.0.2.53')
                self.assertEqual(payload['searchdomain'], 'example.test')
                self.assertEqual({k: config[k] for k in disks}, disks)
                self.assertTrue(set(payload).isdisjoint({'scsi0', 'ide2', 'virtio0', 'sata0', 'efidisk0', 'tpmstate0'}))
        self.assertLess(TASKS.index(task('Apply per-VM configuration')), TASKS.index(task('Start the VM')))

    def test_existing_clone_keeps_nic_mac_and_disks(self):
        # Cover both an existing guest and a clone no-op if the target appeared after lookup.
        for existing in (True, False):
            with self.subTest(existing_at_lookup=existing):
                args, _ = repository_args(existing=existing, clone_changed=False)
                config = self.template_config()
                config['net0'] = 'virtio=02:00:00:00:00:01,bridge=fixturebr8,tag=318,firewall=1'
                original = deepcopy(config)
                _, payload = apply_update(args, config)
                self.assertFalse(args.get('update_unsafe', False))
                self.assertNotIn('net0', payload)
                for key in ('net0', 'scsi0', 'ide2'):
                    self.assertEqual(config[key], original[key])

    def test_ssh_timeout_is_contextual_and_stops_address_publication(self):
        _, variables = repository_args()
        readiness = deepcopy(task('Wait for SSH on the configured address'))
        options = readiness['ansible.builtin.wait_for']
        self.assertEqual(options['port'], 22)
        self.assertEqual(options['timeout'], 300)
        # Zero expires before wait_for's socket loop; no connection is attempted.
        options['timeout'] = 0
        with tempfile.TemporaryDirectory(prefix='homelab-vm-clone-') as directory:
            work = Path(directory)
            marker = work / 'address-published'
            play = [{'name': 'Offline readiness refusal', 'hosts': 'localhost',
                     'connection': 'local', 'gather_facts': False, 'vars': variables,
                     'tasks': [readiness, task('Merge the resolved address into instance facts'),
                               {'name': 'Record continuation', 'ansible.builtin.copy':
                                {'dest': str(marker), 'content': 'continued', 'mode': '0600'}}]}]
            (work / 'play.yml').write_text(yaml.safe_dump(play))
            env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / 'ansible/ansible.cfg'),
                       ANSIBLE_INVENTORY=str(ROOT / 'gate/fixtures/localhost.ini'),
                       ANSIBLE_STDOUT_CALLBACK='default', ANSIBLE_NOCOLOR='1')
            result = subprocess.run([str(Path(sys.executable).with_name('ansible-playbook')),
                                     str(work / 'play.yml')], cwd=ROOT, env=env,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertFalse(marker.exists(), result.stdout)
            self.assertIn('SSH readiness timed out', result.stdout)
            for expected in ('pbs-fixture', '501', 'fixture-node', '198.51.100.50:22',
                             'net0=virtio,bridge=fixturebr7,tag=317',
                             'ipconfig0=ip=198.51.100.50/24,gw=198.51.100.1'):
                self.assertIn(expected, result.stdout)
            self.assertNotIn('fixture-secret', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
