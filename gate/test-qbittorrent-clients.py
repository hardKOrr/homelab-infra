#!/usr/bin/env python3
"""Exercise selected-client wiring and removal against authenticated local APIs."""
import copy
import http.server
import json
import os
import socket
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
        assert self.headers.get('X-Api-Key') == 'arr-key'
        if parsed.path.endswith('/schema'):
            self.respond([{'implementation': 'QBittorrent', 'fields': [
                {'name': x, 'value': ''} for x in ['host', 'port', 'apiKey', 'movieCategory', 'useSsl', 'urlBase', 'username', 'password']]}])
        else:
            assert parsed.path == '/api/v3/downloadclient'
            self.server.client_reads += 1
            self.respond(self.server.clients)

    def do_POST(self):
        assert self.path == '/api/v3/downloadclient'
        assert self.headers.get('X-Api-Key') == 'arr-key'
        row = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert row['name'] == 'qbittorrent'
        fields = {x['name']: x.get('value') for x in row['fields']}
        assert fields['username'] == 'admin' and fields['password'] == 'qbt-password' and fields['movieCategory'] == 'radarr'
        # Real Servarr reads omit values for unset optional fields.
        for field in row['fields']:
            if field['name'] in ['urlBase', 'apiKey']:
                field.pop('value', None)
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
    server.mutations = 0
    server.client_reads = 0
    server.clients = [
        {'id': 1, 'name': 'operator-client', 'implementation': 'Other', 'fields': []},
        {'id': 2, 'name': 'qbittorrent-sibling', 'implementation': 'QBittorrent', 'fields': [
            {'name': 'host', 'value': '192.0.2.5'}, {'name': 'port', 'value': 8085}]},
    ]
    original = copy.deepcopy(server.clients)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # A bound socket without listen() reliably refuses connections and cannot
        # be claimed by another test while the playbook tries the broken consumer.
        with tempfile.TemporaryDirectory() as directory, socket.socket() as unavailable:
            unavailable.bind(('127.0.0.1', 0))
            base = f'http://127.0.0.1:{server.server_port}'
            for action in ['partial-failure', 'deploy', 'converge', 'foreign-entry', 'remove', 'remove-again']:
                operation = 'unwiring' if action.startswith('remove') or action == 'foreign-entry' else 'app-wiring'
                path = Path(directory) / 'clients.yml'
                play = {
                    'hosts': 'localhost', 'gather_facts': False, 'vars': {
                        'instance': 'qbittorrent',
                        'homelabinfra_infra': {'media': {
                            'qbittorrent': {'app': 'qbittorrent', 'host': base},
                            'radarr': {'app': 'radarr', 'host': base},
                            'prowlarr': {'app': 'prowlarr', 'host': 'http://192.0.2.10:9696'},
                        }},
                        'homelabinfra_vault': {'media': {
                            'qbittorrent': {'username': 'admin', 'password': 'qbt-password'}, 'radarr': {'api_key': 'arr-key'}}},
                    }, 'tasks': [{'ansible.builtin.include_tasks': str(
                        repo / f'ansible/tasks/{operation}/qbittorrent-clients.yml')}],
                }
                if action == 'partial-failure':
                    media = play['vars']['homelabinfra_infra']['media']
                    media['radarr-0-broken'] = {
                        'app': 'radarr', 'host': f'http://127.0.0.1:{unavailable.getsockname()[1]}'}
                    media['radarr-1-healthy'] = media.pop('radarr')
                    secrets = play['vars']['homelabinfra_vault']['media']
                    secrets['radarr-0-broken'] = {'api_key': 'arr-key'}
                    secrets['radarr-1-healthy'] = secrets.pop('radarr')
                    play['tasks'] += [{
                        'name': 'Verify consumer failure was collected before subsequent wiring',
                        'ansible.builtin.assert': {'that': [
                            "homelabinfra_degradations | length == 1",
                            "homelabinfra_degradations[0].component == 'qbittorrent → radarr-0-broken'",
                            "media_wire_results | selectattr('state', 'equalto', 'created') | list | length == 1",
                        ]},
                    }, {'name': 'Remaining wiring attempted', 'ansible.builtin.copy': {
                        'dest': str(Path(directory) / 'remaining-wiring'), 'content': 'completed', 'mode': '0600'}},
                        {'ansible.builtin.include_tasks': str(repo / 'ansible/tasks/assert-no-degradations.yml')}]
                path.write_text(yaml.safe_dump([play]))
                before = server.mutations
                reads_before = server.client_reads
                if action == 'foreign-entry':
                    saved = copy.deepcopy(server.clients[2]['fields'])
                    next(x for x in server.clients[2]['fields'] if x['name'] == 'host')['value'] = '192.0.2.99'

                result = subprocess.run([str(ansible), '-i', 'localhost,', '-c', 'local', str(path)],
                                        text=True, capture_output=True, check=False,
                                        env=os.environ | {'ANSIBLE_STDOUT_CALLBACK': 'default'})
                if action == 'partial-failure':
                    assert result.returncode != 0, result.stdout + result.stderr
                    assert (Path(directory) / 'remaining-wiring').read_text() == 'completed'
                    assert 'Degradations | Fail when anything did not work' in result.stdout
                    assert server.client_reads == reads_before + 1
                    assert len(server.clients) == 3 and server.clients[:2] == original
                    continue
                if action == 'foreign-entry':
                    assert result.returncode != 0, result.stdout + result.stderr
                    assert server.mutations == before and len(server.clients) == 3
                    server.clients[2]['fields'] = saved
                    continue
                assert result.returncode == 0, result.stdout + result.stderr
                assert server.clients[:2] == original
                if action in ['converge', 'remove-again']:
                    assert server.mutations == before, action
                assert len(server.clients) == (2 if action.startswith('remove') else 3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print('qBittorrent: unique client wiring, convergence, collected consumer failure and scoped removal passed')


if __name__ == '__main__':
    main()
