#!/usr/bin/env python3
"""Concurrent guest commands keep their output and exit status separate."""

import base64
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]
plays = yaml.safe_load(
    (ROOT / 'ansible/playbooks/maintenance/guest-exec.yml').read_text()
)
task = next(t for p in plays for t in p.get('tasks', []) if t['name'] == 'Run the command')
command = task['ansible.builtin.shell']['cmd']


def run(label, code):
    script = f'printf "{label}-start\\n"; sleep 0.2; printf "{label}-end\\n" >&2; exit {code}'
    result = subprocess.run(
        ['bash', '-c', command],
        env={**os.environ, 'GUEST_EXEC_SCRIPT': base64.b64encode(script.encode()).decode()},
        capture_output=True, check=False,
    )
    assert result.returncode == code, result
    assert base64.b64decode(result.stdout).decode() == f'{label}-start\n{label}-end\n'
    assert not result.stderr, result.stderr


with ThreadPoolExecutor(max_workers=2) as pool:
    results = [pool.submit(run, 'first', 7), pool.submit(run, 'second', 0)]
    for result in results:
        result.result()
print('Guest exec preserves concurrent output and command exit codes')
