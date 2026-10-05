#!/usr/bin/env python3
"""Exercise bootstrap and Enrollment source against disposable localhost services."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('enrollment_test', ROOT / 'gate/test-vaultwarden-enroll.py')
enrollment_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enrollment_test)
OWNER, AUTOMATION = enrollment_test.OWNER, enrollment_test.AUTOMATION


class EnrollmentServer(enrollment_test.FakeVaultwarden):
    def __init__(self):
        super().__init__([])
        self.machine = {}
        self.machine_writes = 0
        self.requests = 0

    def handle(self, method, path, bearer, body):
        self.requests += 1
        if path == '/admin':
            return 200, {}
        if path == '/admin/invite':
            if body['email'] in self.users:
                return 409, {'message': 'User already exists'}
            self.invited.add(body['email'])
            return 200, {}
        if path.startswith('/api/58/storage/keys/project/homelab-infra/vaultwarden-machine/'):
            key = path.rsplit('/', 1)[1]
            if method == 'GET':
                return (200, {}) if key in self.machine else (404, {})
            self.machine_writes += 1
            self.machine[key] = body
            return 201, {}
        return super().handle(method, path, bearer, body)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = {'domain': 'example.com', 'vaultwarden': {
            'owner_email': OWNER, 'automation_email': AUTOMATION},
            'unrelated': {'preserved': True}}
        self.config_file = self.directory / 'infrastructure.yml'
        self.config_file.write_text(yaml.safe_dump(self.config))
        self.before = self.config_file.read_bytes()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('VAULTWARDEN_', 'ANSIBLE_', 'BW_', 'HOMELAB'))}
        self.env.update(ANSIBLE_INVENTORY=str(ROOT / 'gate/fixtures/localhost.ini'),
                        ANSIBLE_CONFIG=str(ROOT / 'ansible/ansible.cfg'),
                        ANSIBLE_NOCOLOR='1')

    def bootstrap(self, **inputs):
        source = (ROOT / 'rundeck/bootstrap-rundeck.sh').read_text()
        block = source.split('# Never enroll an environment override that the owning UI job cannot reproduce.\n', 1)[1].split('\nin_ct sh -c', 1)[0]
        setup = 'set -euo pipefail\nin_ct() { "$@"; }\ndie() { echo "$*" >&2; exit 1; }\n'
        env = {**self.env, 'DEPLOY_VAULTWARDEN': '1', 'REPO_DIR': str(ROOT),
               'VENV_DIR': str(Path(sys.executable).parent.parent),
               'CONFIG_INFRA': str(self.config_file), **inputs}
        result = subprocess.run(['bash', '-c', setup + block +
                                 '\nprintf "%s\\n%s\\n" "$VAULTWARDEN_OWNER_EMAIL" "$VAULTWARDEN_AUTOMATION_EMAIL"'],
                                env=env, capture_output=True, text=True)
        self.assertEqual(self.before, self.config_file.read_bytes())
        return result

    def playbook(self, server, cert, **inputs):
        play = yaml.safe_load((ROOT / 'ansible/playbooks/maintenance/vaultwarden-enroll.yml').read_text())[0]
        play['pre_tasks'][0] = {'name': 'Use isolated infrastructure declaration',
                               'ansible.builtin.set_fact': {'homelabinfra_config': {'infrastructure': self.config}}}
        # Relocate committed helpers for this temporary playbook; keep policy, assertions,
        # ceremony and Key Storage conditions exactly as shipped.
        for task in play['pre_tasks'] + play['tasks']:
            command = task.get('ansible.builtin.command')
            if command:
                command['argv'][-1] = command['argv'][-1].replace(
                    '{{ playbook_dir }}/../../scripts/', str(ROOT / 'ansible/scripts') + '/')
            if 'ansible.builtin.uri' in task:
                task['ansible.builtin.uri']['ca_path'] = str(cert)
        filename = self.directory / 'enroll.yml'
        filename.write_text(yaml.safe_dump([play]))
        accounts = self.directory / 'vaultwarden-accounts.json'
        accounts.write_text(json.dumps({'VAULTWARDEN_OWNER_PASSWORD': 'owner-password-0123456789',
                                       'VAULTWARDEN_AUTOMATION_PASSWORD': 'automation-password-0123456789'}))
        url = f'https://127.0.0.1:{server.server_port}'
        env = {**self.env, 'SSL_CERT_FILE': str(cert), 'BW_SERVER': url,
               'VAULTWARDEN_ADMIN_TOKEN': 'fixture-admin', 'LAB_SECRETS_DIR': str(self.directory),
               'RUNDECK_URL': url, 'RUNDECK_API_TOKEN': 'fixture-token', **inputs}
        result = subprocess.run([str(Path(sys.executable).with_name('ansible-playbook')),
                                 '-i', 'localhost,', '-c', 'local', str(filename)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(self.before, self.config_file.read_bytes())
        return result

    def test_preserved_conflicts_and_repeat_enrollment(self):
        cert, key = self.directory / 'ca.pem', self.directory / 'ca.key'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1'],
                       capture_output=True, check=True)
        fake = EnrollmentServer()
        server = enrollment_test.serve(fake)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        for field in ('OWNER', 'AUTOMATION'):
            inputs = {f'VAULTWARDEN_{field}_EMAIL': 'conflicting@example.com'}
            direct = self.bootstrap(**inputs)
            self.assertNotEqual(direct.returncode, 0)
            self.assertIn('conflicts with config/infrastructure.yml', direct.stderr)
            ui = self.playbook(server, cert, **inputs)
            self.assertNotEqual(ui.returncode, 0)
            self.assertEqual(fake.requests, 0, 'conflict reached an external service')
        identities = self.bootstrap()
        self.assertEqual(identities.returncode, 0, identities.stderr)
        owner, automation = identities.stdout.splitlines()
        self.assertEqual((owner, automation), (OWNER, AUTOMATION))
        first = self.playbook(server, cert, VAULTWARDEN_OWNER_EMAIL=owner,
                              VAULTWARDEN_AUTOMATION_EMAIL=automation)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        state = copy.deepcopy((fake.users, fake.orgs, fake.members, fake.machine))
        writes = (fake.writes, fake.machine_writes)
        self.assertEqual(len(fake.users), 2)
        self.assertEqual(len(fake.orgs), 1)
        self.assertEqual(len(fake.machine), 3)
        second = self.playbook(server, cert)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(state, (fake.users, fake.orgs, fake.members, fake.machine))
        self.assertEqual(writes, (fake.writes, fake.machine_writes))
        requests = fake.requests
        conflict = self.playbook(server, cert, VAULTWARDEN_OWNER_EMAIL='another@example.com')
        self.assertNotEqual(conflict.returncode, 0)
        self.assertEqual(requests, fake.requests)
        self.assertEqual(state, (fake.users, fake.orgs, fake.members, fake.machine))
        self.assertEqual(writes, (fake.writes, fake.machine_writes))

    def test_default_automation_and_missing_owner_do_not_allow_overrides(self):
        self.config['vaultwarden'].pop('automation_email')
        self.config_file.write_text(yaml.safe_dump(self.config))
        self.before = self.config_file.read_bytes()
        result = self.bootstrap(VAULTWARDEN_AUTOMATION_EMAIL=AUTOMATION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [OWNER, AUTOMATION])
        self.config['vaultwarden'].pop('owner_email')
        self.config_file.write_text(yaml.safe_dump(self.config))
        self.before = self.config_file.read_bytes()
        result = self.bootstrap(VAULTWARDEN_OWNER_EMAIL=OWNER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No identity was changed', result.stderr)


if __name__ == '__main__':
    unittest.main()
