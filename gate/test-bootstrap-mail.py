#!/usr/bin/env python3
"""Run real bootstrap authoring/intake against local recording container fixtures."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import yaml

root = Path(__file__).resolve().parents[1]
source = (root / 'rundeck/bootstrap-rundeck.sh').read_text()


def function(name, text=source):
    match = re.search(r'^' + re.escape(name) + r'\(\) \{.*?^\}', text, re.M | re.S)
    assert match, name
    return match.group()


helpers = '\n'.join(function(name) for name in (
    'ask', 'ask_secret', 'net_addr', 'addr_int', 'net_host', 'ask_dns_credential',
    'ask_mail_config', 'write_mail_config', 'stage_mail_credential', 'push_file',
    'ct_file_exists', 'newtmp'))
author = source[source.index('CONFIG_PROXMOX='):source.index('# Resolve the enrollment metadata')]
assert 'ask_mail_config' in author and 'write_mail_config' in author
assert re.search(r'^stage_mail_credential$', source, re.M)
assert 'set +x\nset -euo pipefail' in source
seed_directory = source[source.index('in_ct mkdir -p "$LAB_ETC/secrets.d"'):source.index('if [ -n "$PVE_TOKEN_SECRET" ]; then', source.index('log "Temporary Seed material"'))]
wrapper = (root / 'ansible/scripts/lab-run.sh').read_text()
loader = function('_lab_load_secrets', wrapper)
seed_gate = wrapper[wrapper.index('if [ ! -f "$LAB_VAULT_MARKER" ]'):wrapper.index('# Key-Storage-backed secure options')]

with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'bin').mkdir()
    pct = work / 'bin/pct'
    pct.write_text('''#!/bin/bash
printf '%q ' "$@" >> "$CAPTURE"
printf '\\n' >> "$CAPTURE"
[ "$1" = push ] || exit 1
cp "$3" "$4"
chmod "$6" "$4"
''')
    pct.chmod(0o755)
    pvesh = work / 'bin/pvesh'
    pvesh.write_text('#!/bin/sh\nprintf \'[{"ip":"192.0.2.1","name":"fixture"}]\'\n')
    pvesh.chmod(0o755)
    harness = work / 'fixture.sh'
    harness.write_text('''set +x
set -euo pipefail
info() { printf '%s\\n' "$*"; }
warn() { info "$*"; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
in_ct() {
  printf '%q ' "$@" >> "$CAPTURE"
  printf '\\n' >> "$CAPTURE"
  if [ "$1" = chown ]; then return 0; fi
  "$@"
}
''' + helpers + '\n' + author + '\n' + seed_directory + '\nstage_mail_credential\n' + loader + '''
unset LAB_MAIL_PASSWORD
LAB_SECRETS_FILE="$LAB_ETC/missing.env"
LAB_SECRETS_DIR="$LAB_ETC/secrets.d"
LAB_VAULT_MARKER="$LAB_ETC/state/vault-mode"
''' + seed_gate + '''
# Fixture assertion channel: only this private file may contain the password.
if [ -n "${LAB_MAIL_PASSWORD:-}" ]; then
  (umask 077; printf '%s' "$LAB_MAIL_PASSWORD" > "$LOADED")
fi
''')
    secret = '''fixture-only ' " $(`touch SHOULD_NOT_EXIST`) \\ ; = password'''
    base = dict(os.environ, PATH=str(work / 'bin') + os.pathsep + os.environ['PATH'],
                NONINTERACTIVE='1', VENV_DIR=sys.prefix, VMID='fixture',
                CT_IP='192.0.2.100/24', CT_GW='192.0.2.1', CT_BRIDGE='vmbr0',
                CT_VLAN='0', CT_STORAGE='fixture-storage', NODE_TZ='UTC',
                PVE_API_HOST='192.0.2.1', PVE_NODE='fixture', PVE_USER='fixture@pve',
                PVE_TOKEN_NAME='fixture', LAB_DOMAIN='example.test',
                VAULTWARDEN_OWNER_EMAIL='synthetic-owner@example.test',
                LAB_REVERSE_PROXY='none', LAB_SSO='none', LAB_NOTIFICATIONS='none',
                LAB_DNS='none', LAB_SEED_MODE='1')
    # Isolate fixtures from any operator environment without inspecting its values.
    for key in list(base):
        if key.startswith('LAB_MAIL_') or key == 'FORCE_CONFIG':
            del base[key]

    def run(name, inputs=None, success=True, trace=False):
        directory = work / name
        (directory / 'repo/config').mkdir(parents=True, exist_ok=True)
        (directory / 'etc/secrets.d').mkdir(parents=True, exist_ok=True)
        (directory / 'tmp').mkdir(exist_ok=True)
        (directory / 'ssh.pub').write_text('fixture-public-key\n')
        env = dict(base, REPO_DIR=str(directory / 'repo'), LAB_ETC=str(directory / 'etc'),
                   LAB_MAIL_ENV=str(directory / 'etc/secrets.d/mail.env'),
                   LAB_SSH_KEY=str(directory / 'ssh'), TMPROOT=str(directory / 'tmp'),
                   CAPTURE=str(directory / 'args'), LOADED=str(directory / 'loaded'))
        env.update(inputs or {})
        result = subprocess.run(['bash', '-x' if trace else '-e', str(harness)], env=env,
                                cwd=directory, capture_output=True, text=True)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        public = result.stdout + result.stderr + (directory / 'args').read_text()
        for value in (secret, 'replacement-fixture-password'):
            assert value not in public, 'credential leaked to output/argv'
        for config in (directory / 'repo/config').glob('*.yml'):
            text = config.read_text()
            assert secret not in text and 'LAB_MAIL_PASSWORD' not in text
            data = yaml.safe_load(text)
            assert not any(key in data.get('mail', {}) for key in ('password', 'token', 'api_key'))
        assert not (directory / 'SHOULD_NOT_EXIST').exists(), 'credential executed'
        return directory, result

    directory, _ = run('no-mail', {'LAB_MAIL_PASSWORD': secret}, trace=True)
    assert yaml.safe_load((directory / 'repo/config/infrastructure.yml').read_text())['mail'] == {'provider': 'none'}
    assert not (directory / 'etc/secrets.d/mail.env').exists()

    inputs = dict(LAB_MAIL_PROVIDER='smtp', LAB_MAIL_HOST='smtp.example.test',
                  LAB_MAIL_FROM_ADDRESS='sender@example.test', LAB_MAIL_PASSWORD=secret,
                  LAB_MAIL_FROM_NAME='Fixture "Name"\nsecond line')
    directory, _ = run('smtp', inputs, trace=True)
    config = directory / 'repo/config/infrastructure.yml'
    mail = yaml.safe_load(config.read_text())['mail']
    assert mail == dict(provider='smtp', host='smtp.example.test', port=587,
                        encryption='starttls', from_address='sender@example.test',
                        from_name=inputs['LAB_MAIL_FROM_NAME'], username='sender@example.test')
    sink = directory / 'etc/secrets.d/mail.env'
    assert sink.stat().st_mode & 0o777 == 0o600
    assert sink.parent.stat().st_mode & 0o777 == 0o700
    assert sink.read_text() == 'LAB_MAIL_PASSWORD=' + secret + '\n'
    assert (directory / 'loaded').read_text() == secret, 'literal Seed loading changed value'
    recorded = (directory / 'args').read_text()
    assert 'chown rundeck:rundeck ' + str(sink) in recorded
    assert 'chown rundeck:rundeck ' + str(sink.parent) in recorded

    before = config.read_bytes(), sink.read_bytes()
    run('smtp', dict(LAB_MAIL_PROVIDER='none', LAB_MAIL_PASSWORD='replacement-fixture-password'))
    assert before == (config.read_bytes(), sink.read_bytes()), 'rerun rotated answers/sink'
    run('smtp')  # no new mail inputs or credential needed
    assert before == (config.read_bytes(), sink.read_bytes())

    # Older no-mail authored config stays byte-identical despite new SMTP inputs.
    legacy = work / 'no-mail/repo/config/infrastructure.yml'
    legacy.write_text('domain: example.test\nvaultwarden:\n  owner_email: synthetic-owner@example.test\n')
    before_legacy = legacy.read_bytes()
    run('no-mail', inputs)
    assert legacy.read_bytes() == before_legacy
    assert not (work / 'no-mail/etc/secrets.d/mail.env').exists()

    custom, _ = run('custom', dict(inputs, LAB_MAIL_PORT='465', LAB_MAIL_ENCRYPTION='tls',
                                 LAB_MAIL_USERNAME='fixture-login'))
    custom_mail = yaml.safe_load((custom / 'repo/config/infrastructure.yml').read_text())['mail']
    assert (custom_mail['port'], custom_mail['encryption'], custom_mail['username']) == (465, 'tls', 'fixture-login')

    sink.unlink()
    run('smtp', {'LAB_MAIL_PASSWORD': secret})  # recover missing sink using saved answer
    assert sink.read_bytes() == before[1] and config.read_bytes() == before[0]
    sink.unlink()
    (directory / 'etc/state').mkdir()
    (directory / 'etc/state/vault-mode').touch()
    (directory / 'loaded').unlink()
    run('smtp', {'LAB_MAIL_PASSWORD': secret})
    assert not sink.exists(), 'Vault mode restaged Seed mail'
    run('smtp')  # no password prompt after cutover
    sink.write_text('LAB_MAIL_PASSWORD=' + secret + '\n')
    run('smtp')
    assert not (directory / 'loaded').exists(), 'Vault mode loaded surviving Seed mail'

    for name, override, message in (
        ('missing', {'LAB_MAIL_PASSWORD': ''}, 'LAB_MAIL_PASSWORD is required'),
        ('multiline', {'LAB_MAIL_PASSWORD': 'fixture\nINJECTED=value'}, 'single-line'),
        ('bad-port', {'LAB_MAIL_PORT': '65536'}, 'LAB_MAIL_PORT'),
        ('bad-encryption', {'LAB_MAIL_ENCRYPTION': 'invalid'}, 'LAB_MAIL_ENCRYPTION'),
        ('bad-provider', {'LAB_MAIL_PROVIDER': 'invalid'}, 'LAB_MAIL_PROVIDER'),
    ):
        directory, result = run(name, dict(inputs, **override), success=False)
        assert message in result.stderr
        assert not (directory / 'etc/secrets.d/mail.env').exists()

print('bootstrap mail: no-mail, private/literal intake, validation, rerun and Vault-mode fixtures passed')
