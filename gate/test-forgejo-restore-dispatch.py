#!/usr/bin/env python3
"""Run Forgejo's actual restore dispatcher, replacing only its recovery boundaries.

A multiline templated filename in include_tasks free-form syntax can be parsed as
module options before templating. Exercise Ansible's parser and target selection.
"""
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / '.venvs/homelab-ansible/bin/ansible-playbook'
with tempfile.TemporaryDirectory(prefix='forgejo-dispatch-') as directory:
    root = Path(directory)
    (root / 'restore.yml').write_text(
        (repo / 'ansible/roles/forgejo/tasks/restore.yml').read_text())
    for target in ('guest', 'kubernetes'):
        (root / f'restore-{target}.yml').write_text(yaml.safe_dump([
            {'ansible.builtin.set_fact': {'selected_restore': target}},
        ]))
    tasks = []
    for target, fallback, expected in (
        ('native', 'docker', 'guest'),
        ('docker', 'native', 'guest'),
        ('kubernetes', 'native', 'kubernetes'),
        (None, 'native', 'guest'),
        (None, 'kubernetes', 'kubernetes'),
        (None, None, 'guest'),
    ):
        tasks += [
            {'ansible.builtin.include_tasks': str(root / 'restore.yml'), 'vars': {
                'restore_target_config': {'hosting': {'method': target}} if target else {},
                'app_config': {'hosting': {'method': fallback}} if fallback else {},
            }},
            {'ansible.builtin.assert': {'that': f"selected_restore == '{expected}'"}},
        ]
    playbook = root / 'test.yml'
    playbook.write_text(yaml.safe_dump([{
        'hosts': 'localhost', 'gather_facts': False, 'tasks': tasks,
    }]))
    result = subprocess.run(
        [str(ansible), '-i', 'localhost,', '-c', 'local', str(playbook)],
        env=os.environ | {'ANSIBLE_STDOUT_CALLBACK': 'default', 'ANSIBLE_NOCOLOR': '1'},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
print('Forgejo restore dispatcher: explicit targets and fallback methods passed')
