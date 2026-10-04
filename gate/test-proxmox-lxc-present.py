#!/usr/bin/env python3
"""Execute pinned dispatch/create/update and repository creation guards, no sockets."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = Path.home() / '.ansible/collections'
sys.path.insert(0, str(COLLECTIONS))
from ansible_collections.community.proxmox.plugins.modules import proxmox as upstream
from ansible_collections.community.proxmox.plugins.module_utils.version import LooseVersion


class Finished(BaseException):
    pass


class API:
    """Only the API boundary is stubbed; dispatch, diff and volume handling are real."""
    def __init__(self, records, resources, config, path=()):
        self.records, self._resources, self._config, self.path = records, resources, config, path

    def __getattr__(self, key):
        return API(self.records, self._resources, self._config, self.path + (key,))

    def __call__(self, key):
        return self.__getattr__(str(key))

    def get(self, **kwargs):
        if self.path == ('cluster', 'resources'):
            return self._resources
        if self.path[-1] == 'config':
            return self._config
        raise AssertionError(self.path)

    def put(self, **kwargs):
        self.records.append(('PUT', self.path, kwargs))

    def create(self, **kwargs):
        self.records.append(('POST', self.path, kwargs))
        return 'fixture-upid'


TASKS = yaml.safe_load((ROOT / 'ansible/tasks/proxmox/lxc-create.yml').read_text())
ARG_TASK = next(t for t in TASKS if 'lxc_module_args' in t.get('ansible.builtin.set_fact', {}))


def repository_args(**overrides):
    lxc = dict(state='present', vmid=501, node='fixture-node', hostname='fixture-guest',
               ostemplate='fixture:vztmpl/debian.tar.zst', tags=['_+lab', '_fixture-guest'],
               cores=2, netif={'net0': 'name=eth0,ip=192.0.2.50/24'},
               disk_volume={'storage': 'fixture-rootfs', 'size': 8})
    lxc.update(overrides)
    variables = dict(ARG_TASK['vars'], homelabinfra_instance={'lxc': lxc},
                     homelabinfra_config={'proxmox': {'api_user': 'fixture@pve'}})
    return Templar(loader=DataLoader(), variables=variables).template(
        ARG_TASK['ansible.builtin.set_fact']['lxc_module_args'])


def run(cls, existing=True, resource=None, config=None, args=None):
    records = []
    params = {k: v.get('default') for k, v in upstream.module_args().items()}
    params.update(repository_args() if args is None else args)
    resource = resource if resource is not None else dict(
        vmid=501, node='fixture-node', type='lxc', name='fixture-guest', id='lxc/501')
    config = config if config is not None else dict(
        hostname='fixture-guest', tags='_+lab;_fixture-guest', cmode='tty')

    def finish(**kwargs):
        raise Finished(kwargs)

    obj = cls.__new__(cls)
    obj.module = SimpleNamespace(params=params, exit_json=finish,
                                 fail_json=lambda **kw: finish(failed=True, **kw), warn=lambda m: None)
    obj.params, obj.VZ_TYPE = params, 'lxc'
    obj.proxmox_api = API(records, [resource] if existing else [], config if existing else {})
    # Environmental probes/task completion only; actual production shaping is unmodified.
    obj.version = lambda: LooseVersion('8.0')
    obj.content_check = lambda *a: True
    obj.handle_api_timeout = lambda *a: None
    try:
        obj.run()
    except Finished as result:
        return records, result.args[0]
    raise AssertionError('Module did not return an Ansible result')


class PresentTests(unittest.TestCase):
    def test_pinned_source_reproduction_before_fix(self):
        manifest = json.loads((COLLECTIONS / 'ansible_collections/community/proxmox/MANIFEST.json').read_text())
        self.assertEqual(manifest['collection_info']['version'], '2.0.0')
        self.assertEqual(upstream.module_args()['cmode']['default'], 'default')
        self.assertTrue(upstream.module_args()['update']['default'])
        original = repository_args()
        original.pop('update')
        create, _ = run(upstream.ProxmoxLxcAnsible, False, args=original)
        update, _ = run(upstream.ProxmoxLxcAnsible, args=original)
        self.assertEqual(create[0][0], 'POST')
        self.assertNotIn('cmode', create[0][2])
        self.assertEqual(update[0][0:2], ('PUT', ('nodes', 'fixture-node', 'lxc', '501', 'config')))
        self.assertEqual(update[0][2]['cmode'], 'default')
        print('REPRODUCED upstream 2.0.0: POST omits cmode; existing PUT forwards default')

    def test_repository_console_and_creation_behavior(self):
        for mode in (None, 'default', 'tty', 'console', 'shell'):
            args = repository_args(**({} if mode is None else {'cmode': mode}))
            self.assertNotIn('cmode', args)  # Public allowlist does not support cmode.
            self.assertFalse(repository_args(update=True)['update'])
            self.assertEqual(repository_args(state='absent')['state'], 'present')
            records, result = run(upstream.ProxmoxLxcAnsible, False, args=args)
            self.assertTrue(result['changed'])
            sent = records[0][2]
            self.assertNotIn('cmode', sent)
            self.assertEqual(sent['net0'], args['netif']['net0'])
            self.assertEqual(sent['rootfs'], 'fixture-rootfs:8')
            self.assertEqual(sent['cores'], 2)
            self.assertEqual(sent['tags'], args['tags'])
            self.assertEqual(sent['hostname'], args['hostname'])
            records, result = run(upstream.ProxmoxLxcAnsible, args=args)
            self.assertEqual(records, [])
            self.assertFalse(result['changed'])

    def test_upstream_valid_explicit_creation_console(self):
        # These are supported by upstream, but intentionally remain outside repo inputs.
        for mode in ('tty', 'console', 'shell'):
            args = repository_args()
            args['cmode'] = mode
            records, _ = run(upstream.ProxmoxLxcAnsible, False, args=args)
            self.assertEqual(records[0][2]['cmode'], mode)
            records, result = run(upstream.ProxmoxLxcAnsible, args=args)
            self.assertEqual(records, [])  # Existing console/network/storage left alone.
            self.assertFalse(result['changed'])

    def test_actual_guards_refuse_collisions_failed_inventory_and_races(self):
        guard = next(t for t in TASKS if t['name'] == 'Refuse existing identity at the LXC creation seam')
        post = next(t for t in TASKS if t['name'] == 'Require a newly created LXC before node-local configuration')
        inventory_guard = yaml.safe_load((ROOT / 'ansible/tasks/proxmox/assert-inventory.yml').read_text())
        cases = [
            ('healthy empty', True, {}, True, True),
            ('inventory unavailable', False, {}, True, False),
            ('owned existing', True, {'proxmox_vmid': 501, 'proxmox_hostname': 'fixture-guest',
                                      'proxmox_tags_parsed': ['_+lab'], 'proxmox_vmtype': 'lxc'}, True, False),
            ('unowned VMID collision', True, {'proxmox_vmid': 501}, True, False),
            ('wrong guest type collision', True, {'proxmox_vmid': 501, 'proxmox_vmtype': 'qemu'}, True, False),
            ('wrong node collision', True, {'proxmox_vmid': 501, 'proxmox_node': 'other-node'}, True, False),
            ('hostname collision', True, {'proxmox_vmid': 502, 'proxmox_hostname': 'fixture-guest'}, True, False),
            ('unrelated guest', True, {'proxmox_vmid': 502, 'proxmox_hostname': 'unrelated'}, True, True),
            ('resource appeared after inventory', True, {}, False, False),
        ]
        self.assertEqual(TASKS[0]['ansible.builtin.import_tasks'], 'assert-inventory.yml')
        create_index = next(i for i,t in enumerate(TASKS) if t['name'] == 'Create LXC container')
        self.assertLess(TASKS.index(guard), create_index)
        self.assertEqual(TASKS[create_index + 1], post)
        with tempfile.TemporaryDirectory(prefix='lxc-present-') as directory:
            work = Path(directory)
            env = {k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_', 'PROXMOX_'))}
            env.update(ANSIBLE_CONFIG=str(ROOT / 'ansible/ansible.cfg'), ANSIBLE_STDOUT_CALLBACK='default')
            for label, complete, guest, created, succeeds in cases:
                with self.subTest(label=label):
                    inventory = {'all': {'hosts': {'localhost': {'ansible_connection': 'local'},
                                                  'fixture-host': guest}}}
                    (work / 'inventory.yml').write_text(yaml.safe_dump(inventory))
                    play = [{'hosts': 'localhost', 'gather_facts': False,
                             'vars': {'homelabinfra_proxmox_inventory_complete': complete,
                                      'lxc_module_args': repository_args(),
                                      'create_lxc_result': {'changed': created}},
                             'tasks': inventory_guard + [guard, post]}]
                    (work / 'play.yml').write_text(yaml.safe_dump(play))
                    result = subprocess.run([str(Path(sys.executable).parent / 'ansible-playbook'),
                                             '-i', str(work / 'inventory.yml'), str(work / 'play.yml')],
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)



if __name__ == '__main__':
    unittest.main()
