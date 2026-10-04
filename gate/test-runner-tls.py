#!/usr/bin/env python3
"""Verify bootstrap retains literal, usable CA settings without exporting secrets."""
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'rundeck/preserve-tls-env.py'
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    environment = root / 'lab-run.env'
    certificate = root / 'CA bundle.pem'
    certificate.write_text('fixture CA data')
    assert subprocess.check_output(['python3', str(HELPER), str(environment)], text=True) == ''
    environment.write_text('TOKEN=private-fixture\n' + ''.join(
        f'export {key}={shlex.quote(str(certificate))}\n'
        for key in ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS')))
    first = subprocess.check_output(['python3', str(HELPER), str(environment)], text=True)
    assert 'TOKEN' not in first and 'private-fixture' not in first
    assert len(first.splitlines()) == 4
    environment.write_text(first)
    assert subprocess.check_output(['python3', str(HELPER), str(environment)], text=True) == first
    for invalid in ('SSL_CERT_FILE=relative.pem\n', 'SSL_CERT_FILE=/missing.pem\n',
                    first + first, 'SSL_CERT_FILE=$(touch forbidden)\n'):
        environment.write_text(invalid)
        assert subprocess.run(['python3', str(HELPER), str(environment)], capture_output=True).returncode == 1

# lab-run must export the preserved settings: bootstrap reaches it through sudo, which
# drops the caller's environment, so the env file is the only source of CA trust.
LAB_RUN = ROOT / 'ansible/scripts/lab-run.sh'
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    certificate = root / 'CA bundle.pem'
    certificate.write_text('fixture CA data')
    other = root / 'operator.pem'
    other.write_text('fixture CA data')
    # The explicit Vaultwarden recovery path reaches ansible-playbook without a vault
    # preflight or Proxmox environment, so a stub playbook binary can record the env.
    (root / 'repo/ansible').mkdir(parents=True)
    for part in ('scripts', 'playbooks'):
        (root / 'repo/ansible' / part).symlink_to(ROOT / 'ansible' / part)
    (root / 'state').mkdir()
    (root / 'state/vault-mode').touch()
    (root / 'venv/bin').mkdir(parents=True)
    (root / 'venv/bin/python3').symlink_to(shutil.which('python3'))
    probe = root / 'venv/bin/ansible-playbook'
    probe.write_text('#!/bin/sh\nenv > "$PROBE_OUT"\n')
    probe.chmod(0o755)
    environment = root / 'lab-run.env'
    environment.write_text(f'LAB_REFRESH=0\nTOKEN=private-fixture\n' + subprocess.check_output(
        ['python3', str(HELPER), '/dev/stdin'], text=True, input=''.join(
            f'{key}={shlex.quote(str(certificate))}\n'
            for key in ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS'))))
    base = {'PATH': '/usr/bin:/bin', 'HOME': directory, 'LAB_ENV_FILE': str(environment),
            'LAB_REPO': str(root / 'repo'), 'LAB_VENV': str(root / 'venv'), 'LAB_DOCTOR': '0',
            'LAB_STATE_DIR': str(root / 'state'), 'PROBE_OUT': str(root / 'env.out')}

    def run(extra):
        result = subprocess.run(['bash', str(LAB_RUN), 'playbooks/maintenance/vaultwarden-recovery.yml'],
                                env={**base, **extra}, capture_output=True, text=True)
        seen = {}
        if (root / 'env.out').exists():
            seen = dict(line.split('=', 1) for line in (root / 'env.out').read_text().splitlines()
                        if '=' in line)
            (root / 'env.out').unlink()
        return result, seen

    result, seen = run({'LAB_SEED_MODE': '1'})
    for key in ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS'):
        assert seen.get(key) == str(certificate), (key, result.stderr)
    assert 'TOKEN' not in seen
    _, seen = run({'LAB_SEED_MODE': '1', 'SSL_CERT_FILE': str(other)})
    assert seen['SSL_CERT_FILE'] == str(other) and seen['NODE_EXTRA_CA_CERTS'] == str(certificate)
    certificate.unlink()
    result, seen = run({'LAB_SEED_MODE': '1'})
    assert 'unusable CA settings' in result.stdout + result.stderr
    assert 'SSL_CERT_FILE' not in seen and seen, result.stderr

bootstrap = (ROOT / 'rundeck/bootstrap-rundeck.sh').read_text()
assert bootstrap.index('preserve-tls-env.py') < bootstrap.index('cat > "$LAB_ETC/lab-run.env"')
assert 'cat "$tls_env" >> "$LAB_ETC/lab-run.env"' in bootstrap
print('runner TLS: CA paths, quoted filenames, rerun continuity, private-value exclusion and lab-run export passed')
