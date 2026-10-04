#!/usr/bin/env python3
"""Execute real decommission source against a recording, socket-free PVE fixture."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('decommission', ROOT / 'ansible/files/decommission/lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


class Fixture:
    def __init__(self):
        self.guests = [dict(node='fixture-node', type='lxc', vmid=901, name='fixture-app', tags='_+lab;_ntfy'),
                       dict(node='fixture-node', type='lxc', vmid=902, name='fixture-runner', tags='_+lab;_rundeck'),
                       dict(node='fixture-node', type='qemu', vmid=903, name='foreign', tags='_ntfy')]
        self.configs = {901: dict(tags='_+lab;_ntfy', rootfs='fixture:vm-901-disk-0,size=8G',
                                  mp0='/srv/retained,mp=/data'),
                        902: dict(tags='_+lab;_rundeck', rootfs='fixture:vm-902-disk-0,size=8G'),
                        903: dict(tags='_ntfy', scsi0='fixture:vm-903-disk-0')}
        self.volumes = {901: ['fixture:vm-901-disk-0'], 902: ['fixture:vm-902-disk-0'], 903: ['fixture:vm-903-disk-0']}
        self.state = {'storage': [dict(storage='pbs-homelab', type='pbs', server='192.0.2.20', datastore='fixture-retained', username='fixture@pbs!automation')],
                      'role': [dict(roleid='HomelabInfra', privs='VM.Allocate')],
                      'user': [dict(userid='homelab-infra@pve', comment='homelab-infra platform automation')],
                      'token': [dict(userid='homelab-infra@pve', tokenid='automation', privsep=0)],
                      'acl': [dict(path='/', type='user', ugid='homelab-infra@pve', roleid='HomelabInfra', propagate=1)],
                      'backup': [dict(id='fixture-backup', comment='managed by homelab-infra — vmid list refreshed on bootstrap re-run', vmid='901,902', storage='pbs-homelab')]}
        self.records = {'schema': 1, 'node': 'fixture-node', 'objects': {}}
        self.calls = []
        self.fail_destroy = None
        self.keep_volume = False
        self.change_after_stop = False
        self.active = []

    def api(self, method, endpoint, **params):
        self.calls.append((method, endpoint, params))
        if endpoint == '/cluster/status':
            return [dict(type='node', name='fixture-node', online=1)]
        if endpoint == '/cluster/resources':
            return copy.deepcopy(self.guests)
        if endpoint.endswith('/tasks'):
            return self.active
        if '/tasks/' in endpoint:
            return dict(status='stopped', exitstatus='OK')
        if endpoint == '/nodes/fixture-node/storage':
            return [dict(storage='fixture', content='images,rootdir', active=1)]
        if endpoint.endswith('/content'):
            vmid = int(params['vmid'])
            return [dict(volid=v, vmid=vmid) for v in self.volumes.get(vmid, [])]
        if endpoint.endswith('/config'):
            return copy.deepcopy(self.configs[int(endpoint.split('/')[-2])])
        if endpoint.endswith('/status/current'):
            return dict(status='running')
        if endpoint.endswith('/status/stop'):
            if self.change_after_stop:
                self.configs[int(endpoint.split('/')[-3])]['tags'] = '_foreign'
            return 'UPID:fixture'
        if method == 'delete' and endpoint.startswith('/nodes/'):
            vmid = int(endpoint.split('/')[-1])
            if vmid == self.fail_destroy:
                raise lab.Refused('injected interruption')
            self.guests = [g for g in self.guests if g['vmid'] != vmid]
            if not self.keep_volume:
                self.volumes[vmid] = []
            return 'UPID:fixture'
        if method == 'delete':
            for kind, rows in self.state.items():
                for row in list(rows):
                    ident = lab.identity(kind, row)
                    match = endpoint.endswith('/' + ident) or (kind == 'token' and endpoint.endswith('/token/' + row['tokenid']))
                    match = match or (kind == 'acl' and endpoint == '/access/acl' and params.get('users') == row['ugid'])
                    if match:
                        rows.remove(row)
                        return None
        raise AssertionError((method, endpoint, params))

    def stamp(self, kind):
        row = self.state[kind][0]
        ident = lab.identity(kind, row)
        self.records['objects'][kind + ':' + ident] = dict(source=lab.SOURCES[kind], identity=ident, signature=lab.signature(kind, row))


class DecommissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.saved = {}
        self.stack = []
        def private(path, default=None):
            if path == lab.RECORD:
                return copy.deepcopy(self.fixture.records)
            return copy.deepcopy(self.saved.get(str(path), default))
        for target, value in [('api', self.fixture.api), ('objects', lambda: copy.deepcopy(self.fixture.state)),
                              ('read_private', private), ('write_private', lambda p, v: self.saved.__setitem__(str(p), copy.deepcopy(v))),
                              ('canonical_keys', lambda path=lab.KEYS: []), ('withdraw_keys', lambda *a: None)]:
            patcher = patch.object(lab, target, value)
            patcher.start()
            self.stack.append(patcher)
        self.addCleanup(lambda: [p.stop() for p in reversed(self.stack)])

    def plan(self):
        return dict(lab.plan('fixture-node', '902'), consumers=[{'app': 'ntfy', 'instance': 'ntfy'}, {'app': 'rundeck', 'instance': 'rundeck'}])

    def execute(self, plan):
        with patch.object(lab.os, 'geteuid', return_value=0), patch.object(lab.os, 'uname') as uname, \
             patch.object(lab.Path, 'exists', return_value=True), patch.object(lab.Path, 'resolve', return_value=Path('/etc/pve/priv/authorized_keys')):
            uname.return_value.nodename = 'fixture-node'
            return lab.execute(plan, 'DECOMMISSION ' + lab.digest(plan), 'INDEPENDENT RETENTION VERIFIED', 'ALL CONSUMERS UNWIRED', Path('/fixture/journal'))

    def test_plan_is_read_only_exact_ownership_and_stable(self):
        p = self.plan()
        self.assertEqual(p, self.plan())
        self.assertEqual([g['identity']['vmid'] for g in p['guests']], [901, 902])
        self.assertEqual([g['identity']['vmid'] for g in p['preserved_guests']], [903])
        self.assertEqual(p['guests'][0]['external'][0]['source'], '/srv/retained')
        self.assertTrue(all(c[0] == 'get' for c in self.fixture.calls))
        self.assertFalse(any(o['kind'] == 'storage' for o in p['objects']))

    def test_creation_record_and_rerun_never_replaces_identity(self):
        lab.record('storage', 'pbs-homelab', 'configure-pbs', 'fixture-node', self.fixture.state)
        record = self.saved[str(lab.RECORD)]
        self.assertTrue(lab.owned_object('storage', self.fixture.state['storage'][0], record))
        self.fixture.records = record
        lab.record('storage', 'pbs-homelab', 'configure-pbs', 'fixture-node', self.fixture.state)
        self.fixture.state['storage'][0]['server'] = '192.0.2.21'
        with self.assertRaises(lab.Refused):
            lab.record('storage', 'pbs-homelab', 'configure-pbs', 'fixture-node', self.fixture.state)

    def test_stale_missing_forged_and_shared_provenance_refused(self):
        for kind in lab.SOURCES:
            self.fixture.stamp(kind)
        p = self.plan()
        self.assertEqual(len(p['objects']), 6)
        self.fixture.records['objects']['storage:pbs-homelab']['source'] = 'invented'
        self.fixture.state['role'][0]['privs'] = 'Administrator'
        self.fixture.state['token'].append(dict(userid='homelab-infra@pve', tokenid='foreign', privsep=1))
        p = self.plan()
        selected = {o['kind'] for o in p['objects']}
        self.assertNotIn('storage', selected)
        self.assertNotIn('role', selected)
        self.assertNotIn('user', selected)
        self.assertNotIn('token', selected)

    def test_shared_role_and_foreign_backup_selection_are_excluded(self):
        for kind in lab.SOURCES:
            self.fixture.stamp(kind)
        self.fixture.state['acl'].append(dict(path='/', type='group', ugid='foreign', roleid='HomelabInfra', propagate=1))
        self.fixture.state['backup'][0]['vmid'] += ',903'
        self.assertNotIn('role', {o['kind'] for o in self.plan()['objects']})
        self.assertNotIn('backup', {o['kind'] for o in self.plan()['objects']})

    def test_literal_confirmation_and_retention_before_mutation(self):
        p = self.plan()
        before = len(self.fixture.calls)
        with self.assertRaises(lab.Refused):
            lab.execute(p, 'yes', '', '', Path('/fixture/journal'))
        self.assertEqual(before, len(self.fixture.calls))
        with self.assertRaises(lab.Refused):
            lab.execute(p, 'DECOMMISSION ' + lab.digest(p), '', '', Path('/fixture/journal'))

    def test_execute_runner_last_credentials_after_runner_and_safe_retry(self):
        for kind in lab.SOURCES:
            self.fixture.stamp(kind)
        p = self.plan()
        self.fixture.fail_destroy = 902
        with self.assertRaisesRegex(lab.Refused, 'injected interruption'):
            self.execute(p)
        self.assertEqual([g['vmid'] for g in self.fixture.guests], [902, 903])
        self.assertTrue(self.fixture.state['token'])
        self.fixture.fail_destroy = None
        self.execute(p)
        self.execute(p)
        mutations = [c for c in self.fixture.calls if c[0] == 'delete']
        guests = [c[1] for c in mutations if c[1].startswith('/nodes/')]
        self.assertEqual(guests[0], '/nodes/fixture-node/lxc/901')
        self.assertEqual(guests[-1], '/nodes/fixture-node/lxc/902')
        runner_pos = max(i for i, c in enumerate(mutations) if c[1].endswith('/902'))
        token_pos = next(i for i, c in enumerate(mutations) if '/token/' in c[1])
        self.assertGreater(token_pos, runner_pos)
        self.assertEqual([g['vmid'] for g in self.fixture.guests], [903])
        self.assertTrue(all(c[2].get('purge') == 0 for c in mutations if c[1].startswith('/nodes/')))

    def test_tag_change_between_stop_and_destroy_refuses(self):
        p = self.plan()
        self.fixture.change_after_stop = True
        with self.assertRaises(lab.Refused):
            self.execute(p)
        self.assertFalse(any(c[0] == 'delete' and c[1].startswith('/nodes/') for c in self.fixture.calls))

    def test_disk_leftover_blocks_runner_and_credentials(self):
        p = self.plan()
        self.fixture.keep_volume = True
        with self.assertRaisesRegex(lab.Refused, 'volumes remain'):
            self.execute(p)
        self.assertTrue(any(g['vmid'] == 902 for g in self.fixture.guests))

    def test_active_operation_blocks_first_step(self):
        p = self.plan()
        self.fixture.active = [{'upid': 'UPID:foreign'}]
        with self.assertRaises(lab.Refused):
            self.execute(p)
        self.assertFalse(any(c[0] != 'get' for c in self.fixture.calls))

    def test_changed_storage_provenance_before_action_refuses(self):
        self.fixture.stamp('storage')
        p = self.plan()
        self.fixture.state['storage'][0]['datastore'] = 'foreign-data'
        with self.assertRaises(lab.Refused):
            self.execute(p)
        self.assertTrue(self.fixture.state['storage'])


class KeyTests(unittest.TestCase):
    def test_exact_comment_and_symlink_preserve_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'shared'
            link = Path(tmp) / 'authorized_keys'
            unrelated = 'ssh-ed25519 OPERATOR operator key\nssh-rsa OTHER homelab-infra platform key extra\n'
            target.write_text(unrelated + 'ssh-ed25519 PLATFORM homelab-infra platform key\n')
            link.symlink_to(target)
            lab.withdraw_keys(lab.digest(lab.canonical_keys(link)), link)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(), unrelated)
            lab.withdraw_keys(lab.digest([]), link)

    def test_key_rotation_refuses_and_retains_all_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'keys'
            text = 'ssh-ed25519 NEW homelab-infra platform key\n'
            path.write_text(text)
            with self.assertRaises(lab.Refused):
                lab.withdraw_keys(lab.digest(['ssh-ed25519 OLD homelab-infra platform key\n']), path)
            self.assertEqual(path.read_text(), text)


if __name__ == '__main__':
    unittest.main()
