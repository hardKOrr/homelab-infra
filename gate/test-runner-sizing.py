#!/usr/bin/env python3
"""Exercise actual bootstrap sizing/discovery/writer blocks without a provider."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(os.environ.get('BOOTSTRAP_SCRIPT', ROOT / 'rundeck/bootstrap-rundeck.sh')).read_text()
TUNABLES = '\n'.join(re.findall(r'^CT_(?:CORES|MEMORY|SWAP|DISK)=.*$', SCRIPT, re.M))
helper = re.search(r'^runner_sizing\(\) \{.*?^\}', SCRIPT, re.M | re.S)
HELPER = helper.group(0) if helper else ''
start = SCRIPT.find('# Resolve sizing for the selected VMID')
RESOLVE = SCRIPT[start:SCRIPT.index('# A Proxmox token secret is readable exactly once', start)] if start >= 0 else ''
WRITER = SCRIPT[SCRIPT.index('log "Describe the runner"'):SCRIPT.index('# The provisioning tasks delegate node-local pct/qm waits')]
CREATE = re.search(r'^  pct create .*?(?=^fi\s*$)', SCRIPT, re.M | re.S).group(0)
GUEST = '''cores: 2
memory: 4096
swap: 256
hostname: runner-fixture
rootfs: actual-root:subvol-1234-disk-0,size=32768M
net0: name=eth0,bridge=vmbr4,ip=192.0.2.10/24,gw=192.0.2.1,tag=40,type=veth
tags: _+lab;_-debian;_rundeck
'''


def run(directory, body, overrides=None, guest=GUEST, existing=True):
    env = dict(os.environ)
    for name in ('CT_CORES', 'CT_MEMORY', 'CT_SWAP', 'CT_DISK'):
        env.pop(name, None)
    env.update(REPO_DIR=str(directory), VENV_DIR='/fixture-venv', VMID='1234',
               PVE_NODE='fixture-node', RD_PORT='4440', CT_EXISTS='1' if existing else '0',
               GUEST_CONFIG=guest, CAPTURE=str(directory / 'pct-args'),
               CT_HOSTNAME='unapplied-hostname', CT_IP='198.51.100.9/24',
               CT_GW='198.51.100.1', CT_BRIDGE='vmbr0', CT_VLAN='0',
               CT_STORAGE='unapplied-root', CT_DNS='', CT_VLAN_TAG='',
               MANAGED_TAGS='_+lab;_-debian;_rundeck',
               TEMPLATE_STORAGE='fixture-content', TEMPLATE='fixture.tar.zst')
    env.update(overrides or {})
    stubs = f'''
log() {{ :; }}
info() {{ :; }}
newtmp() {{ mktemp -d "$REPO_DIR/stage.XXXXXX"; }}
push_file() {{ mkdir -p "$(dirname "$2")"; cp "$1" "$2"; }}
pct() {{
  printf '%s\\n' "$@" >> "$CAPTURE"
  case "$1" in
    config) [ "$2" = 1234 ] && [ "$3" = --current ] && [ "$4" = 1 ] || return 90
            printf '%s' "$GUEST_CONFIG" ;;
    create) : ;;
    *) return 91 ;;
  esac
}}
in_ct() {{
  if [ "$1" = chown ]; then return 0; fi
  [ "$1" = /fixture-venv/bin/python3 ] || return 92
  shift
  {shlex.quote(sys.executable)} "$@"
}}
'''
    return subprocess.run(['bash', '-euc', TUNABLES + '\n' + HELPER + '\n' + stubs + body],
                          env=env, capture_output=True, text=True)


with tempfile.TemporaryDirectory() as temp:
    directory = Path(temp)
    path = directory / 'config/apps/rundeck.yml'
    path.parent.mkdir(parents=True)
    observed = dict(vmid=1234, hostname='runner-fixture', node='fixture-node',
                    cores=2, memory=4096, disk=32, storage='actual-root',
                    ip='192.0.2.10/24', gateway='192.0.2.1', bridge='vmbr4', vlan=40)
    authored = {'proxmox': dict(observed, custom={'nested': ['keep']}, onboot=False),
                'app': {'port': 5555, 'service_name': 'operator-service',
                        'checkout_path': '/operator/checkout', 'venv_path': '/operator/venv',
                        'custom': {'retain': True}},
                'routing': {'identity': 'authentik', 'access': 'internal'},
                'backup': {'schedule': 'operator-owned'}}
    original = '# Keep this authored comment too.\n' + yaml.safe_dump(authored, sort_keys=False)
    path.write_text(original)
    result = run(directory, RESOLVE + WRITER)
    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(path.read_text()) == authored, 'existing declaration/overrides were overwritten'
    assert path.read_text() == original, 'unchanged YAML formatting/comments were rewritten'
    first_mtime = path.stat().st_mtime_ns
    assert run(directory, RESOLVE + WRITER).returncode == 0
    assert path.stat().st_mtime_ns == first_mtime, 'second run rewrote unchanged declaration'

    # Repair the previous false defaults but keep every unrelated user-owned answer.
    stale = yaml.safe_load(original)
    stale['proxmox'].update(cores=4, memory=8192, disk=16, bridge='vmbr0', vlan=0,
                           storage='wrong-root', hostname='wrong-host', ip='198.51.100.9/24',
                           gateway='198.51.100.1', vmid=4321, node='wrong-node')
    path.write_text(yaml.safe_dump(stale))
    result = run(directory, RESOLVE + WRITER)
    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(path.read_text()) == authored, 'repair lost unrelated instance answers'

    # Matching explicit sizing is an assertion, never a resize. All mismatches fail
    # before either self-description or any create/set operation can be reached.
    matching = dict(CT_CORES='2', CT_MEMORY='4096', CT_SWAP='256', CT_DISK='32')
    assert run(directory, RESOLVE + WRITER, matching).returncode == 0
    for key, value in dict(CT_CORES='4', CT_MEMORY='8192', CT_SWAP='512', CT_DISK='16').items():
        before = path.read_bytes()
        (directory / 'pct-args').unlink()
        result = run(directory, RESOLVE + WRITER, {key: value})
        assert result.returncode != 0 and 'does not resize' in result.stderr, result.stderr
        assert path.read_bytes() == before
        assert (directory / 'pct-args').read_text().splitlines() == ['config', '1234', '--current', '1']

    # Defaults are effective only at creation, including explicit fresh sizing.
    for overrides, expected in (({}, ('4', '8192', '512', '16')),
                                (matching, ('2', '4096', '256', '32'))):
        (directory / 'pct-args').unlink()
        result = run(directory, RESOLVE + CREATE, overrides, existing=False)
        assert result.returncode == 0, result.stderr
        args = (directory / 'pct-args').read_text().splitlines()
        assert args[0] == 'create'
        assert tuple(args[args.index(key) + 1] for key in ('--cores', '--memory', '--swap')) == expected[:3]
        assert args[args.index('--rootfs') + 1] == 'unapplied-root:' + expected[3]

    # Writer also handles a new instance, missing defaults, untagged net0, and T units.
    path.unlink()
    result = run(directory, WRITER, guest=GUEST.replace(',tag=40', '').replace('32768M', '0.03125T'))
    assert result.returncode == 0, result.stderr
    generated = yaml.safe_load(path.read_text())
    assert generated['proxmox'] == dict(observed, vlan=0)
    assert generated['app']['port'] == 4440 and generated['routing']['identity'] == 'none'
    assert path.stat().st_mode & 0o777 == 0o640
    for content in ('- invalid-root\n', 'app: null\n', 'routing: []\n', 'proxmox: bad\n'):
        path.write_text(content)
        result = run(directory, WRITER)
        assert result.returncode != 0 and 'refusing to overwrite' in result.stderr
        assert path.read_text() == content

    for guest in (GUEST.replace('cores: 2\n', ''), GUEST.replace(',size=32768M', ''),
                  GUEST.replace('32768M', 'invalid')):
        result = run(directory, RESOLVE, guest=guest)
        assert result.returncode != 0 and 'cannot describe runner sizing' in result.stderr
    effective = GUEST.replace('memory: 4096\n', '').replace('swap: 256\n', '')
    result = run(directory, RESOLVE + '\nprintf "%s/%s" "$CT_MEMORY" "$CT_SWAP"', guest=effective)
    assert result.returncode == 0 and result.stdout == '512/512', result.stderr

assert SCRIPT.index('# Resolve sizing for the selected VMID') < SCRIPT.index('log "Discover node facts"')
print('runner sizing: current guest, non-default retention/repair, overrides, creation defaults and safe failures passed')
