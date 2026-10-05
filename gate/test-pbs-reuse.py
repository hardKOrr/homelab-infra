#!/usr/bin/env python3
"""Execute PBS reuse/configuration with recording fixtures, never lab connections."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = 'pbs_fixture'
NIC = 'virtio=02:00:00:00:00:01,bridge=fixturebr7,tag=317,firewall=1,queues=2'


def load(relative):
    return yaml.safe_load((ROOT / relative).read_text())


def fixture_call(module, args, state_path):
    """Every provider/guest command and URI ends here; unknown calls are fatal."""
    path = Path(state_path)
    state = json.loads(path.read_text())
    if module == 'command':
        argv = [str(value) for value in args['argv']] if 'argv' in args else shlex.split(args['cmd'])
        state['calls'].append(argv)
        if argv[:3] == ['qm', 'config', '501']:
            assert argv[3:] in (['--current', '1'], ['--current', '0']), argv
            config = state['guest'] if argv[-1] == '1' else state.get('pending', state['guest'])
            stdout = '\n'.join(f'{key}: {value}' for key, value in config.items())
            result = dict(rc=0, stdout=stdout, stderr='', changed=False)
        elif argv == ['qm', 'start', '501']:
            state['mutations'].append(argv)
            state['status'] = 'running'
            result = dict(rc=0, stdout='', stderr='', changed=True)
        elif argv == ['qm', 'guest', 'exec', '501', '--', '/bin/true']:
            result = dict(rc=0, stdout='', stderr='', changed=False)
        elif argv == ['fixture', 'deployment']:
            state['deployed'] = True
            result = dict(rc=0, stdout='', stderr='', changed=False)
        else:
            raise AssertionError(f'Unexpected command refused: {argv!r}')
    elif module == 'uri':
        method = args.get('method', 'GET')
        endpoint = args['url'].split('/api2/json', 1)[-1]
        state['calls'].append([method, endpoint])
        if method == 'GET' and endpoint == '/config/datastore':
            result = dict(status=200, json={'data': state['datastores']}, changed=False)
        elif method == 'POST' and endpoint == '/config/datastore':
            state['mutations'].append([method, endpoint, args['body']])
            state['datastores'].append(dict(args['body']))
            result = dict(status=200, json={'data': 'UPID:fixture'}, changed=True)
        elif method == 'GET' and endpoint == '/admin/datastore':
            result = dict(status=200, json={'data': [{'store': d['name']} for d in state['datastores']]},
                          changed=False)
        else:
            raise AssertionError(f'Unexpected URI refused: {method} {endpoint}')
    else:
        raise AssertionError(f'Unexpected module refused: {module}')
    path.write_text(json.dumps(state))
    return result


def fixture_modules(node):
    if isinstance(node, dict):
        for module in ('command', 'uri'):
            original = 'ansible.builtin.' + module
            if original in node:
                node['pbs_fixture_' + module] = node.pop(original)
        for value in node.values():
            fixture_modules(value)
    elif isinstance(node, list):
        for value in node:
            fixture_modules(value)


class PbsSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='pbs-source-')
        cls.directory = Path(cls.work.name)
        cls.repo = cls.directory / 'repo'
        cls.plugins = cls.directory / 'plugins'
        cls.plugins.mkdir()
        (cls.directory / 'pbs_fixture.py').write_text(Path(__file__).read_text())
        for module in ('command', 'uri'):
            (cls.plugins / ('pbs_fixture_' + module + '.py')).write_text(
                'import os\nfrom ansible.plugins.action import ActionBase\n'
                'from pbs_fixture import fixture_call\n'
                'class ActionModule(ActionBase):\n'
                '    def run(self, tmp=None, task_vars=None):\n'
                f'        return fixture_call({module!r}, self._task.args, os.environ["PBS_FIXTURE"])\n')
        for relative in (
            'ansible/tasks/proxmox/tag-group.yml',
            'ansible/tasks/proxmox/assert-inventory.yml',
            'ansible/tasks/proxmox/register-nodes.yml',
            'ansible/tasks/proxmox/validate-vm-network.yml',
            'ansible/tasks/proxmox/ensure-guest-running.yml',
            'ansible/tasks/network/resolve-network.yml',
            'ansible/tasks/network/generate-ip.yml',
            'ansible/tasks/recovery/resolve-method.yml',
            'ansible/vars/app-defaults/pbs.yml',
            'catalog/applications.yml',
        ):
            target = cls.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            document = load(relative)
            fixture_modules(document)
            target.write_text(yaml.safe_dump(document, sort_keys=False))
        (cls.repo / 'ansible/tasks/load-user-vars.yml').write_text(
            '- name: Fixture platform inputs\n  ansible.builtin.debug:\n    msg: Offline fixture\n')
        (cls.repo / 'config/apps').mkdir(parents=True)
        cls.env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('ANSIBLE_', 'PROXMOX_', 'RD_', 'BW_', 'BWS_', 'PBS_'))}
        cls.env.update(ANSIBLE_CONFIG=str(ROOT / 'ansible/ansible.cfg'),
                       ANSIBLE_STDOUT_CALLBACK='default', ANSIBLE_NOCOLOR='1',
                       ANSIBLE_ACTION_PLUGINS=str(cls.plugins),
                       ANSIBLE_LOCAL_TEMP=str(cls.directory / 'tmp'),
                       PYTHONPATH=str(cls.directory),
                       PBS_FIXTURE=str(cls.directory / 'state.json'))

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def initial_state(self, nic=NIC, status='running'):
        return dict(guest=dict(name='fixture-pbs-host', tags='_+lab;_-debian;_' + INSTANCE,
                               net0=nic, scsi0='fixture-store:vm-501-disk-0,size=64G',
                               scsi1='fixture-store:vm-501-disk-1,size=128G',
                               ide2='fixture-store:vm-501-cloudinit,media=cdrom', onboot=1),
                    status=status, calls=[], mutations=[], deployed=False,
                    datastore_data={'fixture-A': 'sha256:retained-fixture-A'}, datastores=[])

    def run_play(self, play, state, relative='ansible/playbooks/apps/pbs.yml', tagged_guest=True):
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump([play], sort_keys=False))
        state_path = self.directory / 'state.json'
        state_path.write_text(json.dumps(state))
        inventory = self.directory / 'inventory.ini'
        group = 'lab_app_pbs_fixture' if tagged_guest else 'fixture_unselected'
        inventory.write_text(
            '[' + group + ']\nfixture-pbs-host\n'
            '[all:vars]\nansible_connection=local\n'
            'ansible_python_interpreter=' + sys.executable + '\n')
        ansible = str(Path(sys.executable).with_name('ansible-playbook'))
        result = subprocess.run([ansible, '-i', str(inventory), str(target)], env=self.env,
                                cwd=ROOT, text=True, capture_output=True, timeout=45)
        return result, json.loads(state_path.read_text())

    def run_provision(self, state=None, networks=None, requested='', inventory_tags=None,
                      vmtype='qemu', tagged_guest=True):
        state = deepcopy(state) if state is not None else self.initial_state()
        play = deepcopy(load('ansible/playbooks/apps/pbs.yml')[0])
        fixture_modules(play)
        config = dict(
            proxmox=dict(node='fixture-node', nodes={'fixture-node': '192.0.2.10'},
                         api_host='192.0.2.10'),
            ansible=dict(ssh_user='fixture-user'),
            networks=networks or {'default': {'bridge': 'fixturebr7', 'vlan': 317},
                                 'shared': {'ip_offset': 10}})
        play['vars'] = dict(instance=INSTANCE, homelabinfra_config=config,
                            homelabinfra_proxmox_inventory_complete=True)
        hostvars = dict(proxmox_vmid=501, proxmox_node='fixture-node', proxmox_vmtype=vmtype,
                        proxmox_status=state['status'], ansible_host='198.51.100.50',
                        proxmox_tags_parsed=inventory_tags if inventory_tags is not None
                        else ['_+lab', '_-debian', '_' + INSTANCE])
        play['pre_tasks'].insert(0, {'name': 'Fixture inventory guest identity',
                                    'ansible.builtin.add_host': dict(name='fixture-pbs-host', **hostvars),
                                    'changed_when': False})
        instance_config = {'proxmox': {'network': requested}} if requested else {}
        (self.repo / 'config/apps' / (INSTANCE + '.yml')).write_text(yaml.safe_dump(instance_config))
        play['tasks'] += [
            {'name': 'Verify source deployment group handoff', 'ansible.builtin.assert': {'that': [
                "groups['deploy_' ~ instance] == ['fixture-pbs-host']",
                "hostvars['fixture-pbs-host'].ansible_host == '198.51.100.50'",
                "hostvars['fixture-pbs-host'].app_config.app.port == 8007"]}},
            {'name': 'Record guest deployment continuation',
             'pbs_fixture_command': {'argv': ['fixture', 'deployment']}}]
        return self.run_play(play, state, tagged_guest=tagged_guest)

    def assert_preserved(self, before, after):
        self.assertEqual(after['guest'], before['guest'])
        self.assertEqual(after['datastore_data'], before['datastore_data'])
        self.assertEqual(after['datastores'], before['datastores'])

    def test_model_bridge_and_vlan_mismatch_refuse_running_and_stopped_reuse(self):
        for field, nic, actual in (
            ('model', NIC.replace('virtio=', 'e1000='), 'model=e1000'),
            ('bridge', NIC.replace('fixturebr7', 'fixturebr8'), 'bridge=fixturebr8'),
            ('vlan', NIC.replace(',tag=317', ''), 'vlan=0 (untagged)'),
            ('vlan', NIC.replace('tag=317', 'tag=318'), 'vlan=318'),
        ):
            for status in ('running', 'stopped'):
                with self.subTest(field=field, status=status, nic=nic):
                    before = self.initial_state(nic, status)
                    result, after = self.run_provision(before)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    diagnostic = ' '.join(result.stdout.split())
                    self.assertIn(actual, diagnostic)
                    self.assertIn('expected model=virtio, bridge=fixturebr7, vlan=317', diagnostic)
                    self.assertIn('Refusing start and deployment', diagnostic)
                    self.assertFalse(after['deployed'])
                    self.assertEqual(after['mutations'], [])
                    self.assertFalse(any(call[:2] in (['qm', 'start'], ['qm', 'guest']) for call in after['calls']))
                    self.assert_preserved(before, after)
                    self.assertNotIn('fixture-store:vm-', result.stdout)

    def test_matching_reuse_converges_without_nic_mac_disk_or_data_changes(self):
        for status in ('running', 'stopped'):
            with self.subTest(status=status):
                before = self.initial_state(status=status)
                first, after = self.run_provision(before)
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                self.assertTrue(after['deployed'])
                self.assertEqual(after['status'], 'running')
                self.assertEqual(after['mutations'], [['qm', 'start', '501']] if status == 'stopped' else [])
                self.assert_preserved(before, after)
                after.update(calls=[], mutations=[], deployed=False)
                second, final = self.run_provision(after)
                self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
                self.assertTrue(final['deployed'])
                self.assertEqual(final['mutations'], [])
                self.assertIn('changed=0', second.stdout)
                self.assert_preserved(before, final)

    def test_explicit_network_and_flat_untagged_reuse(self):
        for networks, requested, nic in (
            ({'default': {'bridge': 'fixturebr7', 'vlan': 317}, 'shared': {},
              'isolated': {'bridge': 'fixturebr8', 'vlan': 318}}, 'isolated',
             NIC.replace('fixturebr7', 'fixturebr8').replace('tag=317', 'tag=318')),
            ({'default': {'bridge': 'fixturebr7'}}, '', NIC.replace(',tag=317', '')),
            ({'default': {'bridge': 'fixturebr7', 'vlan': 0}}, '', NIC.replace('tag=317', 'tag=0')),
        ):
            with self.subTest(networks=networks, requested=requested):
                before = self.initial_state(nic)
                result, after = self.run_provision(before, networks, requested)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(after['deployed'])
                self.assertEqual(after['mutations'], [])
                self.assert_preserved(before, after)

    def test_pending_correct_nic_cannot_hide_current_mismatch_and_inverse(self):
        for current, pending in ((NIC.replace(',tag=317', ''), NIC),
                                 (NIC, NIC.replace(',tag=317', ''))):
            with self.subTest(current=current, pending=pending):
                before = self.initial_state(current, 'stopped')
                before['pending'] = dict(before['guest'], net0=pending)
                result, after = self.run_provision(before)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(after['mutations'], [])
                self.assertEqual(after['pending'], before['pending'])
                self.assert_preserved(before, after)
                self.assertFalse(after['deployed'])

    def test_missing_effective_bridge_refuses_creation_and_reuse_before_mutation(self):
        for default in ({}, {'bridge': ''}, {'bridge': None}):
            for tagged_guest, status in ((True, 'running'), (True, 'stopped'), (False, 'stopped')):
                with self.subTest(default=default, tagged_guest=tagged_guest, status=status):
                    before = self.initial_state(status=status)
                    networks = {'default': default, 'shared': {'ip_offset': 10}}
                    result, after = self.run_provision(before, networks, tagged_guest=tagged_guest)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    diagnostic = ' '.join(result.stdout.split())
                    self.assertIn('must declare a bridge for network shared', diagnostic)
                    self.assertIn('networks.default.bridge', diagnostic)
                    self.assertIn('before template creation, guest start or deployment', diagnostic)
                    self.assertFalse(after['deployed'])
                    self.assertEqual(after['calls'], [])
                    self.assertEqual(after['mutations'], [])
                    self.assertEqual(after['status'], before['status'])
                    self.assert_preserved(before, after)

    def test_invalid_declaration_or_actual_guest_identity_refuses_before_start(self):
        for values in ({'requested': 'missing'}, {'inventory_tags': ['_' + INSTANCE]}, {'vmtype': 'lxc'}):
            with self.subTest(values=values):
                result, after = self.run_provision(**values)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(after['mutations'], [])
                self.assertFalse(after['deployed'])
        for change in ({'tags': '_other'}, {'template': 1}, {'net0': ''},
                       {'net0': NIC.replace('tag=317', 'tag=invalid')},
                       {'net0': NIC + ',tag=317'}):
            with self.subTest(change=change):
                before = self.initial_state(status='stopped')
                before['guest'].update(change)
                result, after = self.run_provision(before)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(after['mutations'], [])
                self.assertFalse(after['deployed'])
                self.assert_preserved(before, after)

    def run_datastore(self, mode='reuse', datastores=None, declared_path='/mnt/fixture-backup'):
        state = self.initial_state()
        state['datastores'] = datastores if datastores is not None else [dict(name='homelab', path='/mnt/fixture-backup')]
        source = load('ansible/tasks/bootstrap/configure-pbs.yml')
        boundary = next(i for i, task in enumerate(source) if task['name'] == 'PBS | Apply retention settings on datastore')
        tasks = deepcopy(source[:boundary])
        fixture_modules(tasks)
        variables = dict(pbs_api_host='https://pbs.example.test:8007', pbs_api_token_id='fixture-id',
                         pbs_api_token_secret='fixture-secret', pbs_datastore_mode=mode,
                         homelabinfra_config=dict(
                             infrastructure={'backups': {'datastore_path': declared_path}},
                             proxmox=dict(api_host='192.0.2.10', api_user='fixture@pve',
                                          api_token_id='fixture-id', api_token_secret='fixture-secret')))
        play = dict(name='Offline source datastore configuration', hosts='localhost', connection='local',
                    gather_facts=False, vars=variables, tasks=tasks)
        result, after = self.run_play(play, state)
        self.assertEqual(after['datastore_data'], state['datastore_data'])
        self.assertNotIn('fixture-secret', result.stdout)
        return result, after

    def test_recovered_datastore_reuses_registration_without_initialisation(self):
        result, state = self.run_datastore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state['mutations'], [])
        self.assertEqual(state['datastores'], [dict(name='homelab', path='/mnt/fixture-backup')])
        self.assertIn(['GET', '/admin/datastore'], state['calls'])

    def test_missing_changed_or_ambiguous_recovery_store_fails_closed(self):
        for datastores in ([], [dict(name='homelab', path='/mnt/wrong')],
                           [dict(name='homelab', path='/mnt/fixture-backup')] * 2):
            with self.subTest(datastores=datastores):
                result, state = self.run_datastore(datastores=datastores)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('refusing datastore creation or initialisation', result.stdout)
                self.assertEqual(state['mutations'], [])
                self.assertEqual(state['datastores'], datastores)

    def test_trailing_slash_paths_match_in_both_modes_without_rewriting_registration(self):
        for mode in ('create', 'reuse'):
            for registered, declared in (
                ('/mnt/fixture-backup/', '/mnt/fixture-backup'),
                ('/mnt/fixture-backup', '/mnt/fixture-backup///'),
                ('/mnt/fixture-backup///', '/mnt/fixture-backup/'),
                ('/', '///'),
            ):
                with self.subTest(mode=mode, registered=registered, declared=declared):
                    stores = [dict(name='homelab', path=registered)]
                    result, state = self.run_datastore(mode=mode, datastores=stores, declared_path=declared)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(state['mutations'], [])
                    self.assertEqual(state['datastores'], stores)
                    self.assertIn('changed=0', result.stdout)
                    self.assertIn(['GET', '/admin/datastore'], state['calls'])

    def test_genuine_or_missing_path_in_both_modes_refuses_before_mutation(self):
        for mode in ('create', 'reuse'):
            for registration, declared in (
                (dict(name='homelab', path='/mnt/wrong/'), '/mnt/fixture-backup'),
                (dict(name='homelab', path='/mnt/fixture-backup/../fixture-backup/'), '/mnt/fixture-backup'),
                (dict(name='homelab'), '/'),
                (dict(name='homelab', path=''), '/'),
            ):
                with self.subTest(mode=mode, registration=registration, declared=declared):
                    stores = [registration]
                    result, state = self.run_datastore(mode=mode, datastores=stores, declared_path=declared)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('mode=' + mode, result.stdout)
                    self.assertEqual(state['mutations'], [])
                    self.assertEqual(state['datastores'], stores)
                    self.assertEqual(state['calls'], [['GET', '/config/datastore']])

    def test_first_install_creation_and_existing_store_convergence_remain_callable(self):
        result, state = self.run_datastore(mode='create', datastores=[])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state['mutations'], [['POST', '/config/datastore',
                                              dict(name='homelab', path='/mnt/fixture-backup')]])
        result, state = self.run_datastore(mode='create')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state['mutations'], [])
        result, state = self.run_datastore(mode='create', datastores=[dict(name='homelab', path='/mnt/wrong')])
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state['mutations'], [])

    def test_pbs_has_no_invented_recovery_method(self):
        for method in ('pbs_guest', 'native', 'project_managed'):
            with self.subTest(method=method):
                play = dict(name='Offline PBS capability refusal', hosts='localhost', connection='local',
                            gather_facts=False, vars=dict(
                                recovery_app_config=load('ansible/vars/app-defaults/pbs.yml')['pbs_defaults'],
                                recovery_instance=INSTANCE, recovery_app='pbs', recovery_method_requested=method),
                            tasks=[{'ansible.builtin.import_tasks': '../../tasks/recovery/resolve-method.yml'},
                                   {'pbs_fixture_command': {'argv': ['fixture', 'deployment']}}])
                result, after = self.run_play(play, self.initial_state())
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('has no declared recovery capability', result.stdout)
                self.assertEqual(after['calls'], [])
                self.assertEqual(after['mutations'], [])
                self.assertFalse(after['deployed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
