#!/usr/bin/env python3
"""Actual inventory/parser and caller tasks, with requests transport replaced in process.

No sockets or provider requests: a recording adapter supplies PVE-shaped responses.
The initial requests differential is deliberately run before corrected source tests.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import requests
import yaml

REPO = Path(__file__).resolve().parents[1]
BIN = Path(sys.executable).parent


def install_transport():
    from urllib.parse import urlsplit

    fixture = json.loads(Path(os.environ['INVENTORY_FIXTURE']).read_text())
    node_reads = 0

    def send(adapter, request, **kwargs):
        nonlocal node_reads
        path = urlsplit(request.url).path
        with open(os.environ['INVENTORY_RECORD'], 'a') as stream:
            stream.write(json.dumps({'method': request.method, 'path': path,
                                     'verify': kwargs['verify']}) + '\n')
        assert request.method == 'GET', 'fixture refuses mutation'
        response = requests.Response()
        response.status_code = 200
        response.request = request
        response.url = request.url
        if path.endswith('/nodes'):
            node_reads += 1
            if fixture.get('failure') or (fixture.get('refresh_failure') and node_reads > 1):
                raise requests.exceptions.SSLError('provider-free inventory failure')
            data = [{'node': 'fixture-node', 'type': 'node',
                     'status': 'unknown' if fixture.get('unknown') else ('offline' if fixture.get('offline') else 'online')}]
            if fixture.get('mixed'):
                data.append({'node': 'fixture-online', 'type': 'node', 'status': 'online'})
            if fixture.get('malformed_node'):
                data[0]['status'] = 'invalid-status'
        elif path.endswith('/pools') or path.endswith('/qemu') or path.endswith('/snapshot'):
            data = []
            if path.endswith('/pools') and fixture.get('partial_failure'):
                response.status_code = 503  # Fail after hosts and groups were populated.
        elif path.endswith('/lxc'):
            data = [] if fixture.get('empty') else [
                {'name': 'caddy', 'vmid': 501, 'status': 'stopped', 'tags': '_+lab;_caddy'}]
            if fixture.get('ambiguous'):
                data.append({'name': 'caddy-other', 'vmid': 502, 'status': 'stopped', 'tags': '_+lab;_caddy'})
            if fixture.get('invalid_identity'):
                data[0]['vmid'] = 0
            if fixture.get('malformed'):
                data = None
        elif path.endswith('/status/current'):
            data = {'status': 'stopped'}
        elif path.endswith('/config'):
            data = {'hostname': 'caddy', 'tags': '_caddy' if fixture.get('unowned') else '_+lab;_caddy;_.stack+caddy',
                    'net0': 'name=eth0,ip=192.0.2.50/24'}
        else:
            raise AssertionError('Unexpected fixture request path')
        response._content = json.dumps({'data': data}).encode()
        return response

    requests.adapters.HTTPAdapter.send = send


def trust_differential():
    recorded = []

    def send(adapter, request, **kwargs):
        recorded.append(kwargs['verify'])
        response = requests.Response()
        response.status_code = 200
        return response

    with patch.object(requests.adapters.HTTPAdapter, 'send', send):
        for env, explicit, want in [
            ({}, {}, False),
            ({'REQUESTS_CA_BUNDLE': '/fixture/requests.pem'}, {}, '/fixture/requests.pem'),
            ({'CURL_CA_BUNDLE': '/fixture/curl.pem'}, {}, '/fixture/curl.pem'),
            ({'REQUESTS_CA_BUNDLE': '/fixture/requests.pem', 'CURL_CA_BUNDLE': '/fixture/curl.pem'},
             {}, '/fixture/requests.pem'),
            ({'REQUESTS_CA_BUNDLE': '/fixture/requests.pem'}, {'verify': False}, False),
        ]:
            with patch.dict(os.environ, env, clear=True):
                session = requests.Session()
                session.verify = False
                session.get('https://provider-free.invalid', **explicit)
                assert recorded[-1] == want, (recorded[-1], want)
    print('PASS: requests environment/session differential (source semantics only)', flush=True)


def main():
    trust_differential()
    with tempfile.TemporaryDirectory(prefix='inventory-safety-') as directory:
        work = Path(directory)
        (work / 'sitecustomize.py').write_text(
            'from test_proxmox_inventory_safety import install_transport\ninstall_transport()\n')
        # Import name for sitecustomize, without modifying the installed toolchain.
        (work / 'test_proxmox_inventory_safety.py').write_text(Path(__file__).read_text())
        fixture = work / 'fixture.json'
        record = work / 'record.jsonl'
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('PROXMOX_', 'ANSIBLE_')) and key not in (
                   'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'SSL_CERT_FILE')}
        env.update({
            'ANSIBLE_CONFIG': str(REPO / 'ansible/ansible.cfg'),
            'ANSIBLE_LOCAL_TEMP': str(work / 'ansible-tmp'),
            'ANSIBLE_REMOTE_TEMP': str(work / 'remote-tmp'),
            'ANSIBLE_STDOUT_CALLBACK': 'default', 'ANSIBLE_DISPLAY_OK_HOSTS': 'true',
            'PYTHONPATH': str(work), 'INVENTORY_FIXTURE': str(fixture),
            'INVENTORY_RECORD': str(record), 'PROXMOX_API_HOST': 'provider-free.invalid',
            'PROXMOX_API_USER': 'fixture@pve', 'PROXMOX_API_TOKEN_ID': 'fixture',
            'PROXMOX_API_TOKEN_SECRET': 'fixture-placeholder',
            'REQUESTS_CA_BUNDLE': '/fixture/declared-ca.pem',
            'CURL_CA_BUNDLE': '/fixture/fallback-ca.pem',
        })

        def run(args, state, extra=None):
            fixture.write_text(json.dumps(state))
            record.write_text('')
            result = subprocess.run([str(BIN / args[0]), *args[1:]], cwd=REPO,
                                    env=env | (extra or {}), text=True, capture_output=True)
            calls = [json.loads(line) for line in record.read_text().splitlines()]
            assert all(call['method'] == 'GET' for call in calls)
            return result, calls

        source = str(REPO / 'ansible/inventory/proxmox.yml')
        for state in ({}, {'empty': True}):
            result, calls = run(['ansible-inventory', '-i', source, '--list'], state)
            assert result.returncode == 0, result.stderr
            inventory = json.loads(result.stdout)
            assert inventory['_meta']['hostvars']['fixture-node'][
                'homelabinfra_proxmox_inventory_complete'] is True
            assert inventory.get('lab_app_caddy', {}).get('hosts', []) == ([] if state else ['caddy'])
            assert calls and all(call['verify'] is False for call in calls)
        print('PASS: real inventory healthy empty/tagged identity and explicit independent TLS policy')

        for state in ({'offline': True}, {'offline': True, 'mixed': True},
                      {'unknown': True}, {'unknown': True, 'mixed': True}):
            result, calls = run(['ansible-inventory', '-i', source, '--list'], state)
            assert result.returncode == 0, result.stderr
            inventory = json.loads(result.stdout)
            node = inventory['_meta']['hostvars']['fixture-node']
            assert node['homelabinfra_proxmox_inventory_complete'] is False
            assert node['homelabinfra_proxmox_inventory_offline_nodes'] == ['fixture-node']
            assert not any('/nodes/fixture-node/' in call['path'] for call in calls), calls
            assert inventory.get('lab_app_caddy', {}).get('hosts', []) == (['caddy'] if state.get('mixed') else [])
        result, _ = run(['ansible-inventory', '-i', source, '--list'], {'malformed_node': True})
        assert result.returncode != 0, 'malformed node state must stay fatal'

        # Execute actual diagnostic refresh/report tasks, without platform credential
        # loading or guest/provider probes. Offline coverage must be explicit in output.
        status = yaml.safe_load((REPO / 'ansible/playbooks/maintenance/status.yml').read_text())[0]
        status.pop('pre_tasks')
        for task in status['tasks']:
            if 'ansible.builtin.import_tasks' in task:
                task['ansible.builtin.import_tasks'] = str(REPO / 'ansible/tasks/proxmox/report-inventory.yml')
        status_file = work / 'status.yml'
        status_file.write_text(yaml.safe_dump([status]))
        for state in ({'offline': True}, {'unknown': True}, {'unknown': True, 'mixed': True}):
            result, _ = run(['ansible-playbook', '-i', source, str(status_file)], state)
            assert result.returncode == 0, result.stdout + result.stderr
            assert 'fixture-node' in result.stdout and 'their state is unknown' in result.stdout

        ascent = yaml.safe_load((REPO / 'ansible/playbooks/maintenance/verify-ascent.yml').read_text())
        decision = {'hosts': 'localhost', 'gather_facts': False,
                    'vars': {'_va_down': [], '_va_unreachable': [], '_va_notready': []},
                    'tasks': [task for task in ascent[-1]['tasks'] if task.get('name') in (
                        'Decide whether the ascent is clean', 'Report the ascent',
                        'Fail when the lab did not fully come back')]}
        ascent_file = work / 'ascent.yml'
        ascent_file.write_text(yaml.safe_dump([status, decision]))
        for state in ({'empty': True}, {'offline': True}, {'unknown': True}, {'unknown': True, 'mixed': True}):
            result, _ = run(['ansible-playbook', '-i', source, str(ascent_file)], state)
            assert (result.returncode == 0) is (not (state.get('offline') or state.get('unknown'))), result.stdout + result.stderr
            if state.get('offline') or state.get('unknown'):
                assert 'The ascent is incomplete' in result.stdout and 'fixture-node' in result.stdout
        print('PASS: offline/unknown inventory/status diagnostics run; ascent reports incomplete, malformed nodes stay fatal')

        # True keeps declared environment trust. Upstream and local clients are tested
        # with the same request transport and repository options.
        configured = yaml.safe_load(Path(source).read_text())
        upstream = work / 'upstream.proxmox.yml'
        upstream.write_text(yaml.safe_dump(configured | {'plugin': 'community.proxmox.proxmox'}))
        result, calls = run(['ansible-inventory', '-i', str(upstream), '--list'], {})
        assert result.returncode == 0, result.stderr
        assert calls and all(call['verify'] == '/fixture/declared-ca.pem' for call in calls)
        print('PASS: pinned upstream source reproduces environment override with validate_certs=false')
        configured['validate_certs'] = True
        verified = work / 'verified.proxmox.yml'
        verified.write_text(yaml.safe_dump(configured))
        result, calls = run(['ansible-inventory', '-i', str(verified), '--list'], {})
        assert result.returncode == 0, result.stderr
        assert calls and all(call['verify'] == '/fixture/declared-ca.pem' for call in calls)
        print('PASS: verification=true retains declared CA environment')

        # Execute the actual Caddy selection tasks through add_host. Subsequent remote
        # deploy/start tasks are outside this fixture, so no substitute provider exists.
        caddy = yaml.safe_load((REPO / 'ansible/playbooks/apps/caddy.yml').read_text())[0]
        provision = next(task for task in caddy['tasks'] if task.get('name') == 'Provision new LXC')
        selection = caddy['tasks'][:4]
        for task in selection:
            if 'ansible.builtin.include_tasks' in task:
                task['ansible.builtin.include_tasks'] = str(
                    (REPO / 'ansible/playbooks/apps' / task['ansible.builtin.include_tasks']).resolve())
        play = {'hosts': 'localhost', 'gather_facts': False, 'vars': {
            'instance': 'caddy', 'app_config': {},
            'homelabinfra_config': {'ansible': {'ssh_user': 'fixture'},
                                   'networks': {'default': {'cidr': '192.0.2.0/24'}}}},
            'tasks': selection + [
                {'ansible.builtin.assert': {'that': [
                    "_existing_hosts == (['caddy'] if expect_existing else [])",
                    "not expect_existing or hostvars['caddy'].proxmox_vmid == 501",
                    "not expect_existing or groups['deploy_caddy'] == ['caddy']"]}},
                {'ansible.builtin.import_tasks': str(REPO / 'ansible/tasks/network/generate-ip.yml'),
                 'when': provision['when']},
                {'ansible.builtin.debug': {'msg': 'SELECTION_AND_ALLOCATION_COMPLETE'}}]}
        (work / 'ansible/playbooks/apps').mkdir(parents=True)
        (work / 'ansible/scripts').symlink_to(REPO / 'ansible/scripts', target_is_directory=True)
        playbook = work / 'ansible/playbooks/apps/selection.yml'
        playbook.write_text(yaml.safe_dump([play]))
        for state in ({}, {'empty': True}, {'failure': True}, {'partial_failure': True},
                      {'refresh_failure': True}, {'offline': True}, {'offline': True, 'mixed': True},
                      {'unknown': True}, {'unknown': True, 'mixed': True},
                      {'malformed_node': True}, {'unowned': True}, {'malformed': True},
                      {'ambiguous': True}, {'invalid_identity': True}):
            result, calls = run(['ansible-playbook', '-i', source, str(playbook),
                                 '-e', json.dumps({'expect_existing': not state.get('empty')})], state)
            success = not any(state.get(key) for key in (
                'failure', 'partial_failure', 'refresh_failure', 'offline', 'unknown', 'unowned', 'malformed',
                'ambiguous', 'invalid_identity', 'malformed_node'))
            assert (result.returncode == 0) is success, result.stdout + result.stderr
            assert ('SELECTION_AND_ALLOCATION_COMPLETE' in result.stdout) is success
            if not success:
                assert 'TASK [Set network selector]' not in result.stdout
        # A parsed localhost source must not mask an unavailable dynamic source.
        result, _ = run(['ansible-playbook', '-i', 'localhost,', '-i', source,
                         str(playbook)], {'failure': True})
        assert result.returncode != 0 and 'SELECTION_AND_ALLOCATION_COMPLETE' not in result.stdout
        result, _ = run(['ansible-playbook', '-i', 'localhost,', '-i', source,
                         str(playbook)], {'partial_failure': True}, {
                             'ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED': 'false'})
        assert result.returncode != 0 and 'TASK [Set network selector]' not in result.stdout
        # Static inventory alone cannot authorize absence-based allocation/reuse.
        result, _ = run(['ansible-playbook', '-i', 'localhost,', str(playbook)], {})
        assert result.returncode != 0 and 'TASK [Set network selector]' not in result.stdout
        print('PASS: real Caddy reuse/allocation tasks reject failed, partial, refresh, offline/unknown,')
        print('      unowned and unsupported inventory; healthy empty allocation remains supported')

        # Execute each supported native/stack caller's actual refresh, tag lookup and
        # absence/reuse decision, rather than reproducing those expressions in Python.
        callers = []
        paths = sorted((REPO / 'ansible/playbooks/apps').glob('*.yml')) + [
            REPO / 'ansible/tasks/stack/find-or-create-host.yml',
            REPO / 'ansible/tasks/stack/resolve-existing-host.yml']
        for path in paths:
            if path.name == 'remove.yml':
                continue  # Removal's backend selector is covered by its own contract.
            loaded = yaml.safe_load(path.read_text())
            tasks = loaded if '/tasks/' in str(path) else loaded[0].get('tasks', [])
            decisions = [(index, task) for index, task in enumerate(tasks)
                         if 'groups[tag_group_name]' in str(task.get('ansible.builtin.set_fact', {}))]
            if not decisions:
                continue
            index, decision = decisions[0]
            if not any(task.get('ansible.builtin.meta') == 'refresh_inventory' for task in tasks[:index]):
                continue  # Migration-only plays refresh in pre_tasks; not provisioning callers.
            refresh = next(task for task in tasks[:index]
                           if task.get('ansible.builtin.meta') == 'refresh_inventory')
            original_lookup = next(task for task in tasks[:index]
                                   if str(task.get('ansible.builtin.include_tasks', '')).endswith('proxmox/tag-group.yml'))
            lookup = dict(original_lookup)
            lookup['ansible.builtin.include_tasks'] = str(REPO / 'ansible/tasks/proxmox/tag-group.yml')
            selected = next(key for key, value in decision['ansible.builtin.set_fact'].items()
                            if 'groups[tag_group_name]' in str(value))
            # Preserve source ordering so moving a guard ahead of refresh regresses.
            selected_tasks = [lookup if task is original_lookup else task for task in tasks[:index + 1]
                              if any(task is selected_task for selected_task in (refresh, original_lookup, decision))]
            callers.append({'name': path.name, 'hosts': 'localhost', 'gather_facts': False,
                            'vars': {'instance': 'caddy', '_stack_id': 'caddy', '_resolve_stack_id': 'caddy'},
                            'tasks': selected_tasks + [{'ansible.builtin.assert': {'that': [
                                selected + " == (['caddy'] if expect_existing else [])"]}}]})
        assert len(callers) >= 15, 'supported caller coverage unexpectedly shrank'
        caller_file = work / 'callers.yml'
        caller_file.write_text(yaml.safe_dump(callers))
        for state in ({}, {'empty': True}, {'refresh_failure': True}):
            result, _ = run(['ansible-playbook', '-i', source, str(caller_file), '-e',
                             json.dumps({'expect_existing': not state.get('empty')})], state)
            assert (result.returncode == 0) is (not state.get('refresh_failure', False)), result.stdout + result.stderr
        print(f'PASS: actual absence/reuse decisions for {len(callers)} native and stack callers')


if __name__ == '__main__':
    main()
