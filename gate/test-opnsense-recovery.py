#!/usr/bin/env python3
"""Exercise native form sessions, XML export and fail-closed restore uploads."""
import importlib.util
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'transfer', ROOT / 'ansible/roles/opnsense/files/config-transfer.py')
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)
XML = b'<opnsense><system><hostname>opnsense-drill</hostname><user><name>root</name></user></system><interfaces><lan><ipaddr>192.0.2.1</ipaddr></lan></interfaces></opnsense>'
TOKEN = '<input type="hidden" name="csrf-token" value="session-value">'


class Response:
    def __init__(self, request, data):
        self.url, self.data = request.full_url, data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.data


class NativeForm:
    def __init__(self, result=b'The configuration has been restored.'):
        self.calls = []
        self.logged_in = False
        self.result = result

    def open(self, req, timeout):
        self.calls.append(req)
        path = urllib.parse.urlsplit(req.full_url).path
        if req.data:
            assert b'session-value' in req.data
        if path == '/index.php':
            if req.data:
                values = urllib.parse.parse_qs(req.data.decode())
                assert values['passwordfld'] == ['private-password']
                self.logged_in = True
            result = TOKEN.encode()
        else:
            assert self.logged_in
            if req.data is None:
                result = (TOKEN + '<input name="conffile" type="file">').encode()
            elif b'filename="config.xml"' in req.data:
                assert XML in req.data
                assert b'name="rebootafterrestore"' in req.data
                result = self.result
            else:
                assert b'donotbackuprrd=1' in req.data
                result = XML
        return Response(req, result)


class RecoveryTest(unittest.TestCase):
    def test_appliance_has_no_proxy_wiring(self):
        defaults = yaml.safe_load((ROOT / 'ansible/vars/app-defaults/opnsense.yml').read_text())['opnsense_defaults']
        self.assertEqual(defaults['routing']['proxy'], 'none')

    def test_production_destruction_guards(self):
        production = yaml.safe_load((ROOT / 'ansible/tasks/removal/guard-guest-destruction.yml').read_text())
        assertions = [task for task in production if 'ansible.builtin.assert' in task
                      and task['name'] in (
                          'Validate the explicit guest destruction request before unwiring',
                          'Verify ownership and non-shared tags before unwiring')]
        self.assertEqual(len(assertions), 2)
        ansible = Path.home() / '.venvs/homelab-ansible/bin/ansible-playbook'
        for enabled, owned in ((True, True), (False, True), (True, False)):
            with self.subTest(enabled=enabled, owned=owned), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                inventory = {'all': {'hosts': {'opnsense-drill': {
                    'proxmox_vmtype': 'qemu', 'proxmox_vmid': 123,
                    'proxmox_name': 'opnsense-drill',
                    'proxmox_tags_parsed': ['_+lab', '_opnsense-drill'] if owned else ['_opnsense-drill']}}}}
                (directory / 'inventory.yml').write_text(yaml.safe_dump(inventory))
                play = [{'hosts': 'localhost', 'gather_facts': False, 'connection': 'local',
                         'vars': {'instance': 'opnsense-drill', 'destroy_guest': True,
                                  'delete_data': True, '_rm_kind': 'appliance',
                                  '_rm_destroy_guest_host': 'opnsense-drill',
                                  'app_config': {'recovery': {'native': {'controller': enabled}},
                                                 'backup': {'application_consistent': True}}},
                         'tasks': assertions}]
                (directory / 'guard.yml').write_text(yaml.safe_dump(play))
                result = subprocess.run([str(ansible), '-i', str(directory / 'inventory.yml'),
                                         str(directory / 'guard.yml')], capture_output=True, text=True,
                                        env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK='default'))
                self.assertEqual(result.returncode == 0, enabled and owned,
                                 result.stdout + result.stderr)

    def settings(self, path, action):
        return dict(instance='opnsense-drill', address='192.0.2.1',
                    password='private-password', path=str(path), action=action)

    def test_current_export_private_and_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.xml'
            with patch.object(transfer.urllib.request, 'build_opener', return_value=NativeForm()):
                transfer.transfer(self.settings(path, 'backup'))
            self.assertEqual(path.read_bytes(), XML)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_native_restore_and_rejected_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.xml'
            path.write_bytes(XML)
            for result in (b'The configuration has been restored.', b'Could not restore.',
                           b'The configuration has been restored. Postponing reboot.'):
                with patch.object(transfer.urllib.request, 'build_opener', return_value=NativeForm(result)):
                    if result == b'The configuration has been restored.':
                        transfer.transfer(self.settings(path, 'restore'))
                    else:
                        with self.assertRaises(ValueError):
                            transfer.transfer(self.settings(path, 'restore'))

    def test_wrong_identity_and_malformed_xml_never_contact_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.xml'
            for data in (b'<broken>', XML.replace(b'opnsense-drill', b'other'),
                         XML.replace(b'192.0.2.1', b'192.0.2.2')):
                path.write_bytes(data)
                native = NativeForm()
                with patch.object(transfer.urllib.request, 'build_opener', return_value=native):
                    with self.assertRaises(Exception):
                        transfer.transfer(self.settings(path, 'restore'))
                self.assertEqual(native.calls, [])


if __name__ == '__main__':
    unittest.main()
