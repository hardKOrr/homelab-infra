#!/usr/bin/env python3
"""Exercise selected-client wiring and removal against authenticated local APIs."""
import copy
import http.server
import json
import os
import subprocess
import tempfile
import threading
import urllib.parse
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / '.venvs/homelab-ansible/bin/ansible-playbook'


class API(http.server.BaseHTTPRequestHandler):
    def respond(self, value, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == '/api':
            args = urllib.parse.parse_qs(parsed.query)
            assert args['apikey'] == ['sab-key']
            if args['mode'] == ['get_cats']:
                self.respond({'categories': sorted(self.server.categories)})
            else:
                assert args['mode'] == ['set_config']
                self.server.categories.add(args['name'][0])
                self.server.mutations += 1
                self.respond({'config': {'categories': [{'name': args['name'][0]}]}})
            return
        assert self.headers.get('X-Api-Key') == 'arr-key'
        if parsed.path.endswith('/schema'):
            self.respond([{'implementation': 'Sabnzbd', 'fields': [
                {'name': x, 'value': ''} for x in ['host', 'port', 'apiKey', 'movieCategory', 'useSsl']]}])
        else:
            assert parsed.path == '/api/v3/downloadclient'
            self.respond(self.server.clients)

    def do_POST(self):
        assert self.path == '/api/v3/downloadclient'
        assert self.headers.get('X-Api-Key') == 'arr-key'
        row = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert row['name'] == 'sabnzbd'
        fields = {x['name']: x.get('value') for x in row['fields']}
        assert fields['apiKey'] == 'sab-key' and fields['movieCategory'] in self.server.categories
        self.server.clients.append(row | {'id': 3})
        self.server.mutations += 1
        self.respond(row | {'id': 3}, 201)

    def do_DELETE(self):
        assert self.path == '/api/v3/downloadclient/3'
        assert self.headers.get('X-Api-Key') == 'arr-key'
        self.server.clients = [x for x in self.server.clients if x['id'] != 3]
        self.server.mutations += 1
        self.respond({})

    def log_message(self, *_args):
        pass


def main():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), API)
    server.categories = {'operator-category'}
    server.mutations = 0
    server.clients = [
        {'id': 1, 'name': 'operator-client', 'implementation': 'Other', 'fields': []},
        {'id': 2, 'name': 'sabnzbd-sibling', 'implementation': 'Sabnzbd', 'fields': [
            {'name': 'host', 'value': '192.0.2.5'}, {'name': 'port', 'value': 8085}]},
    ]
    original = copy.deepcopy(server.clients)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            base = f'http://127.0.0.1:{server.server_port}'
            for action in ['deploy', 'converge', 'foreign-entry', 'remove', 'remove-again']:
                operation = 'unwiring' if action.startswith('remove') or action == 'foreign-entry' else 'app-wiring'
                path = Path(directory) / 'clients.yml'
                path.write_text(yaml.safe_dump([{
                    'hosts': 'localhost', 'gather_facts': False, 'vars': {
                        'instance': 'sabnzbd',
                        'homelabinfra_infra': {'media': {
                            'sabnzbd': {'app': 'sabnzbd', 'host': base},
                            'radarr': {'app': 'radarr', 'host': base},
                            'prowlarr': {'app': 'prowlarr', 'host': 'http://192.0.2.10:9696'},
                        }},
                        'homelabinfra_vault': {'media': {
                            'sabnzbd': {'api_key': 'sab-key'}, 'radarr': {'api_key': 'arr-key'}}},
                    }, 'tasks': [{'ansible.builtin.include_tasks': str(
                        repo / f'ansible/tasks/{operation}/sabnzbd-clients.yml')}],
                }]))
                before = server.mutations
                if action == 'foreign-entry':
                    saved = copy.deepcopy(server.clients[2]['fields'])
                    next(x for x in server.clients[2]['fields'] if x['name'] == 'host')['value'] = '192.0.2.99'

                result = subprocess.run([str(ansible), '-i', 'localhost,', '-c', 'local', str(path)],
                                        text=True, capture_output=True, check=False,
                                        env=os.environ | {'ANSIBLE_STDOUT_CALLBACK': 'default'})
                if action == 'foreign-entry':
                    assert result.returncode != 0, result.stdout + result.stderr
                    assert server.mutations == before and len(server.clients) == 3
                    server.clients[2]['fields'] = saved
                    continue
                assert result.returncode == 0, result.stdout + result.stderr
                assert server.clients[:2] == original
                assert 'operator-category' in server.categories
                if action in ['converge', 'remove-again']:
                    assert server.mutations == before, action
                assert len(server.clients) == (2 if action.startswith('remove') else 3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print('SABnzbd: category creation, unique client wiring, convergence and scoped removal passed')


if __name__ == '__main__':
    main()
