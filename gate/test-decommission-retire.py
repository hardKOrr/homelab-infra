#!/usr/bin/env python3
"""Exercise PBS stamp retirement through execute() without node or private-file access."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'decommission_lab', Path(__file__).resolve().parents[1] / 'ansible/files/decommission/lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def encoded(value):
    return json.dumps(value, sort_keys=True, indent=2).encode()


class Retirement(unittest.TestCase):
    def setUp(self):
        self.node = '<pve-node>'
        self.journal = Path('/fake/journal.json')
        self.state = {
            'storage': [{'storage': 'pbs-homelab', 'type': 'pbs', 'server': '192.0.2.10',
                         'datastore': 'homelab', 'username': 'backup@pbs', 'fingerprint': 'old'}],
            'user': [{'userid': 'homelab-infra@pve', 'comment': 'managed by homelab-infra',
                      'enable': 1, 'expire': 0}],
            'token': [{'userid': 'homelab-infra@pve', 'tokenid': 'automation', 'privsep': 0}],
            'acl': [{'path': '/', 'type': 'user', 'ugid': 'homelab-infra@pve',
                     'roleid': 'HomelabInfra', 'propagate': 1}],
            'role': [{'roleid': 'HomelabInfra'}], 'backup': [],
        }
        stamps = {}
        targets = []
        for kind, rows in self.state.items():
            for row in rows:
                ident = lab.identity(kind, row)
                stamp = {'source': lab.SOURCES[kind], 'identity': ident,
                         'signature': lab.signature(kind, row)}
                stamps[kind + ':' + ident] = stamp
                targets.append({'kind': kind, 'identity': ident, 'signature': stamp['signature']})
        stamps['storage:unrelated'] = {'source': 'configure-pbs', 'identity': 'unrelated',
                                       'signature': 'c' * 64, 'extra': ['preserve', 1]}
        self.original = {'schema': 1, 'node': self.node, 'objects': stamps,
                         'retired': [{'key': 'storage:previous', 'stamp': {'signature': 'b' * 64},
                                      'retired_at': 123}]}
        self.files = {lab.RECORD: encoded(self.original)}
        self.writes = []
        self.mutations = []
        self.tamper = False
        self.manifest = {
            'schema': 1, 'node': self.node, 'runner': '<runner-vmid>',
            'consumers': [{'instance': 'rundeck'}],
            # The runner is already gone: its absence and empty disk inventory are rechecked.
            'guests': [{'identity': {'vmid': '<runner-vmid>', 'node': self.node},
                        'tags': ['_+lab', '_rundeck'], 'volumes': []}],
            'objects': targets, 'preserved_guests': [], 'excluded_objects': [],
            'keys_hash': lab.digest([]), 'foreign_keys_hash': lab.digest([]),
        }
        patches = [
            patch.object(lab, 'api', side_effect=self.api),
            patch.object(lab, 'read_private', side_effect=self.read),
            patch.object(lab, 'write_private', side_effect=self.write),
            patch.object(lab.os, 'geteuid', return_value=0),
            patch.object(lab.os, 'uname', return_value=SimpleNamespace(nodename=self.node)),
            patch.object(lab.Path, 'exists', return_value=True),
            patch.object(lab, 'KEYS', SimpleNamespace(resolve=lambda: Path('/etc/pve/priv/authorized_keys'))),
            patch.object(lab, 'foreign_keys', return_value=[]),
            patch.object(lab, 'canonical_keys', return_value=[]),
            patch.object(lab, 'withdraw_keys'),
            patch.object(lab.time, 'time', return_value=456),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def read(self, path, default=None):
        if path in self.files:
            return json.loads(self.files[path])
        lab.require(default is not None, 'Missing fake private record.')
        return copy.deepcopy(default)

    def write(self, path, value):
        self.files[path] = encoded(value)
        self.writes.append(path)
        if path == self.journal and 'storage:pbs-homelab' in value['completed']:
            # Retirement must have been persisted before the storage step is completed.
            self.assertNotIn('storage:pbs-homelab', self.read(lab.RECORD)['objects'])

    def api(self, method, endpoint, **params):
        if method == 'get':
            if endpoint == '/cluster/status':
                return [{'type': 'node', 'name': self.node, 'online': 1}]
            if endpoint == '/cluster/resources' or endpoint.endswith('/tasks'):
                return []
            if endpoint == '/nodes/' + self.node + '/storage':
                if self.tamper:
                    record = self.read(lab.RECORD)
                    record['unexpected'] = True
                    self.files[lab.RECORD] = encoded(record)
                return []
            kind = {'/storage': 'storage', '/cluster/backup': 'backup',
                    '/access/users': 'user', '/access/roles': 'role', '/access/acl': 'acl'}.get(endpoint)
            if kind:
                return copy.deepcopy(self.state[kind])
            if endpoint == '/access/users/homelab-infra@pve/token':
                return [{k: v for k, v in row.items() if k != 'userid'}
                        for row in copy.deepcopy(self.state['token'])]
        elif method in ['delete', 'set']:
            kind = {'/storage/pbs-homelab': 'storage', '/access/users/homelab-infra@pve': 'user',
                    '/access/users/homelab-infra@pve/token/automation': 'token',
                    '/access/roles/HomelabInfra': 'role', '/access/acl': 'acl'}[endpoint]
            self.state[kind] = []
            self.mutations.append(kind)
            return None
        self.fail(f'Unexpected API call: {method} {endpoint} {params}')

    def execute(self):
        return lab.execute(self.manifest, 'DECOMMISSION ' + lab.digest(self.manifest),
                           'INDEPENDENT RETENTION VERIFIED', 'ALL CONSUMERS UNWIRED', self.journal)

    def test_retirement_rerun_and_new_fingerprint(self):
        result = self.execute()
        after = self.read(lab.RECORD)
        key = 'storage:pbs-homelab'
        self.assertNotIn(key, after['objects'])
        self.assertEqual(after['retired'][-1],
                         {'key': key, 'stamp': self.original['objects'][key], 'retired_at': 456})
        for other, stamp in self.original['objects'].items():
            if other != key:
                self.assertEqual(encoded(after['objects'][other]), encoded(stamp))
        self.assertEqual(encoded(after['retired'][:-1]), encoded(self.original['retired']))
        # All credential operations ran with the refreshed provenance and real guards.
        self.assertEqual(self.mutations, ['storage', 'token', 'acl', 'user', 'role'])
        snapshot = copy.deepcopy((self.files, self.state, self.writes, self.mutations))
        self.assertEqual(self.execute(), result)
        self.assertEqual((self.files, self.state, self.writes, self.mutations), snapshot)
        new_storage = {'storage': 'pbs-homelab', 'type': 'pbs', 'fingerprint': 'new'}
        self.state['storage'] = [new_storage]
        lab.record('storage', 'pbs-homelab', 'configure-pbs', self.node)
        registered = self.read(lab.RECORD)
        self.assertEqual(registered['objects'][key]['signature'], lab.signature('storage', new_storage))
        del registered['objects'][key]
        self.assertEqual(encoded(registered), encoded(after))

    def test_retirement_checks_even_when_storage_already_absent(self):
        for field, value in [('source', 'wrong'), ('identity', 'wrong'), ('signature', '0' * 64),
                             ('retired', {})]:
            with self.subTest(field=field):
                invalid = copy.deepcopy(self.original)
                if field == 'retired':
                    invalid[field] = value
                else:
                    invalid['objects']['storage:pbs-homelab'][field] = value
                self.files = {lab.RECORD: encoded(invalid)}
                self.state['storage'] = []
                with self.assertRaises(lab.Refused):
                    self.execute()
                self.assertEqual(self.files[lab.RECORD], encoded(invalid))
                self.assertNotIn('storage:pbs-homelab', self.read(self.journal, {'completed': []})['completed'])

    def test_later_provenance_change_still_refused(self):
        self.tamper = True
        with self.assertRaisesRegex(lab.Refused, 'Creation provenance changed during execution'):
            self.execute()
        self.assertEqual(self.mutations, ['storage'])


if __name__ == '__main__':
    unittest.main()
