#!/usr/bin/env python3
"""Run tasks/recovery/new-target-overlay.yml: a destination=new restore target gets its
own database name, role and host port, and never the source's."""
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / '.venvs/homelab-ansible/bin/ansible-playbook'
if not ansible.exists():
    ansible = Path('ansible-playbook')
defaults = {'plane_defaults': {
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
    ('plane-b', 'new', {'app': {'port': 9000, 'database': {'name': 'mine', 'role': 'mine'}}},
     "o == {}"),
    ('plane-b', 'existing', {}, "o == {}"),
]
tasks = []
for target, dest, authored, check in cases:
    tasks += [
        {'ansible.builtin.include_tasks': str(repo / 'ansible/tasks/recovery/new-target-overlay.yml'),
         'vars': base | {'_ra_target': target, '_ra_destination': dest, '_target_config': authored}},
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
print('Restore new target overlay: own database login and port passed')
