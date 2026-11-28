#!/usr/bin/env python3
"""Run tasks/recovery/new-target-overlay.yml: a destination=new restore target gets its
own database name, role and host port, and never the source's."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / '.venvs/homelab-ansible/bin/ansible-playbook'
if not ansible.exists():
    ansible = Path('ansible-playbook')
defaults = {'plane_defaults': {
    'routing': {'subdomain': 'plane', 'proxy': 'internal'},
    'hosting': {'method': 'docker'},
    'app': {'port': 8093, 'database': {'provider': 'postgresql', 'instance': 'pg',
                                       'name': 'plane', 'role': 'plane'}}}}
base = {'_ra_app': 'plane', '_ra_defaults_key': 'plane_defaults',
        '_app_defaults': defaults, '_instance_config': {}}
cases = [
    ('plane-b', 'new', {}, "o.app.database.name == 'plane_b' and o.app.database.role == 'plane_b'"
     " and o.app.port != 8093 and o.app.port | int >= 20000"),
    ('1plane-b', 'new', {}, "o.app.database.name == 'r_1plane_b'"),
    ('x' * 40, 'new', {}, "o.app.database.name | length == 32 and o.app.database.name == o.app.database.role"),
    ('plane-b', 'new', {}, "o.routing.subdomain == 'plane-b'"),
    ('plane-b', 'new', {'app': {'port': 9000, 'database': {'name': 'mine', 'role': 'mine'}}},
     "o.app.database.name == 'mine' and o.app.port == 9000"),
    ('plane-b', 'existing', {}, "o == {}"),
    # Only database.name authored: the role is still generated, never the source's 'plane'.
    ('plane-b', 'new', {'app': {'database': {'name': 'mine'}}},
     "o.app.database.name == 'mine' and o.app.database.role == 'plane_b'"),
    # The source's own item already uses the derived login: take a suffix.
    ('plane-blue', 'new', {}, "o.app.database.role == 'plane_blue_new'",
     {'homelabinfra_vault': {'apps': {'plane': {'database_name': 'plane_blue', 'database_user': 'plane_blue'}}}, 'instance': 'plane'}),
]
refused = [
    ('plane-b', {'app': {'database': {'name': 'plane'}}}),
    ('plane-b', {'app': {'database': {'role': 'plane'}}}),
    ('plane-b', {'app': {'port': 8093}}),
]
tasks = []
for target, dest, authored, check, *extra in cases:
    tasks += [
        {'ansible.builtin.include_tasks': str(repo / 'ansible/tasks/recovery/new-target-overlay.yml'),
         'vars': base | (extra[0] if extra else {}) | {'_ra_target': target, '_ra_destination': dest, '_target_config': authored}},
        {'ansible.builtin.assert': {'that': check}, 'vars': {'o': '{{ _ra_new_target_overlay }}'}},
    ]
with tempfile.TemporaryDirectory(prefix='new-target-overlay-') as directory:
    playbook = Path(directory) / 'test.yml'
    playbook.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False, 'tasks': tasks}]))
    result = subprocess.run(
        [str(ansible), '-i', 'localhost,', '-c', 'local', str(playbook)],
        env=os.environ | {'ANSIBLE_STDOUT_CALLBACK': 'default', 'ANSIBLE_NOCOLOR': '1'},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
for target, authored in refused:
    play = [{'hosts': 'localhost', 'gather_facts': False, 'tasks': [
        {'ansible.builtin.include_tasks': str(repo / 'ansible/tasks/recovery/new-target-overlay.yml'),
         'vars': base | {'_ra_target': target, '_ra_destination': 'new', '_target_config': authored}}]}]
    with tempfile.TemporaryDirectory(prefix='new-target-overlay-') as directory:
        playbook = Path(directory) / 'refuse.yml'
        playbook.write_text(yaml.safe_dump(play))
        result = subprocess.run([str(ansible), '-i', 'localhost,', '-c', 'local', str(playbook)],
                                env=os.environ | {'ANSIBLE_NOCOLOR': '1'}, capture_output=True, text=True)
        assert result.returncode != 0 and 'uses' in result.stdout, authored
# End to end: write the target's config, load it over the defaults and run the shared
# database consumer with provision.yml stubbed to record its inputs.
with tempfile.TemporaryDirectory(prefix='new-target-provision-') as directory:
    root = Path(directory)
    shutil.copy(repo / 'ansible/tasks/database/deploy-consumer.yml', root / 'deploy-consumer.yml')
    (root / 'provision.yml').write_text(yaml.safe_dump([
        {'ansible.builtin.set_fact': {
            'seen': {'item': '{{ database_provision_app_instance }}',
                     'name': '{{ database_provision_config.name }}',
                     'role': '{{ database_provision_config.role }}'},
            'database_provision_connection': {
                'provider': 'postgresql', 'host': 'h', 'port': 5432, 'name': '{{ database_provision_config.name }}',
                'user': '{{ database_provision_config.role }}', 'password': 'x'}}}]))
    (root / 'config').mkdir()
    play = [{'hosts': 'localhost', 'gather_facts': False, 'vars': base | {
        '_ra_target': 'plane-b', '_ra_destination': 'new', '_target_config': {}}, 'tasks': [
        {'ansible.builtin.include_tasks': str(repo / 'ansible/tasks/recovery/new-target-overlay.yml')},
        {'ansible.builtin.include_tasks': str(repo / 'ansible/tasks/recovery/write-new-target-config.yml'),
         'vars': {'new_target_config_dir': str(root / 'config')}},
        {'ansible.builtin.include_vars': {'file': str(root / 'config/plane-b.yml'), 'name': '_written'}},
        {'ansible.builtin.set_fact': {'instance': 'plane-b', 'app_config':
            "{{ _app_defaults.plane_defaults | combine(_written, recursive=True) }}"}},
        {'ansible.builtin.include_tasks': str(root / 'deploy-consumer.yml')},
        {'ansible.builtin.assert': {'that': [
            "seen.item == 'plane-b'", "seen.name == 'plane_b'", "seen.role == 'plane_b'",
            "app_config.app.port | int != 8093", "app_config.app.port | int >= 20000"]}},
    ]}]
    playbook = root / 'e2e.yml'
    playbook.write_text(yaml.safe_dump(play))
    result = subprocess.run([str(ansible), '-i', 'localhost,', '-c', 'local', str(playbook)],
                            env=os.environ | {'ANSIBLE_NOCOLOR': '1'}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
# A partial authored file (only database.name) is completed: deploy reads the same role,
# port and route the restore resolved, and the authored name is kept.
with tempfile.TemporaryDirectory(prefix='new-target-partial-') as directory:
    root = Path(directory)
    (root / 'plane-b.yml').write_text(yaml.safe_dump({'app': {'database': {'name': 'mine'}}}))
    play = [{'hosts': 'localhost', 'gather_facts': False, 'vars': base | {
        '_ra_target': 'plane-b', '_ra_destination': 'new'}, 'tasks': [
        {'ansible.builtin.include_vars': {'file': str(root / 'plane-b.yml'), 'name': '_target_config'}},
        {'ansible.builtin.include_tasks': str(repo / 'ansible/tasks/recovery/new-target-overlay.yml')},
        {'ansible.builtin.include_tasks': str(repo / 'ansible/tasks/recovery/write-new-target-config.yml'),
         'vars': {'new_target_config_dir': str(root)}},
        {'ansible.builtin.include_vars': {'file': str(root / 'plane-b.yml'), 'name': '_written'}},
        {'ansible.builtin.assert': {'that': [
            "(_app_defaults.plane_defaults | combine(_written, recursive=True)).app.database.name == 'mine'",
            "(_app_defaults.plane_defaults | combine(_written, recursive=True)).app.database.role == 'plane_b'",
            "(_app_defaults.plane_defaults | combine(_written, recursive=True)).app.port == _ra_new_target_overlay.app.port",
            "(_app_defaults.plane_defaults | combine(_written, recursive=True)).routing.subdomain == 'plane-b'"]}},
    ]}]
    playbook = root / 'partial.yml'
    playbook.write_text(yaml.safe_dump(play))
    result = subprocess.run([str(ansible), '-i', 'localhost,', '-c', 'local', str(playbook)],
                            env=os.environ | {'ANSIBLE_NOCOLOR': '1'}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
print('Restore new target overlay: own database login and port passed')
