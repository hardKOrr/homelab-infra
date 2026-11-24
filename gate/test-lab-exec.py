#!/usr/bin/env python3
"""Lab execution targets use the shared resolver, preserving stdin and exit status."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / 'lab-exec').write_text((repo / 'tools/lab-exec').read_text())
    (root / 'labaccess.py').write_text('''
import json, sys
def command_or_stdin(args):
    return (' '.join(args), None) if args else (None, sys.stdin.buffer.read())
def lab_values(): return {}
def runner(values): return ('runner-node', '123')
def any_node(values): return 'any-node'
def pct_exec(vmid, command): return 'pct ' + vmid + ' ' + (command or 'bash -s')
def run_on_node(ip, remote, script):
    print(json.dumps([ip, remote, script.decode() if script else None]))
    return 7
''')
    for args, expected in [([], ['runner-node', 'pct 123 bash -s', 'echo probe']),
                           (['--node'], ['any-node', 'bash -s', 'echo probe']),
                           (['--runner-node'], ['runner-node', 'bash -s', 'echo probe']),
                           (['--runner-node', 'true'], ['runner-node', 'true', None])]:
        result = subprocess.run([sys.executable, str(root / 'lab-exec'), *args],
                                input='echo probe', text=True, capture_output=True)
        assert result.returncode == 7, result.stderr
        assert json.loads(result.stdout) == expected
print('Lab execution: runner, any node and runner node preserve routing, stdin and status')
