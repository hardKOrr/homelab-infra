#!/usr/bin/env python3
"""Runner key reconciliation preserves operator keys and pmxcfs symlinks."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
text = (root / 'rundeck/bootstrap-rundeck.sh').read_text()
function = re.search(r'^authorize_pve_key\(\) \{.*?^\}', text, re.M | re.S).group(0)
with tempfile.TemporaryDirectory() as temp:
    directory = Path(temp)
    target = directory / 'etc/pve/priv/authorized_keys'
    target.parent.mkdir(parents=True)
    link = directory / 'root/.ssh/authorized_keys'
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    operator = 'ssh-ed25519 OPERATOR owner key\nssh-rsa CLUSTER cluster member\n'
    target.write_text(operator + 'ssh-ed25519 STALE homelab-infra platform key\n')
    new_key = 'ssh-ed25519 CURRENT homelab-infra platform key'
    command = function + '\nauthorize_pve_key "$PUBKEY" "homelab-infra platform key" "$AUTH"'
    env = dict(os.environ, PUBKEY=new_key, AUTH=str(link))
    subprocess.run(['bash', '-euc', command], env=env, check=True)
    assert link.is_symlink(), 'cluster authorized_keys symlink replaced'
    assert target.read_text() == operator + new_key + '\n'
    modified = target.stat().st_mtime_ns
    subprocess.run(['bash', '-euc', command], env=env, check=True)
    assert target.stat().st_mtime_ns == modified, 'idempotent rerun rewrote authorization'
print('runner SSH: unrelated keys, shared-file symlink and rerun continuity passed')
