#!/usr/bin/env python3
"""Verify bootstrap retains literal, usable CA settings without exporting secrets."""
from pathlib import Path
import shlex
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
bootstrap = (ROOT / 'rundeck/bootstrap-rundeck.sh').read_text()
assert bootstrap.index('preserve-tls-env.py') < bootstrap.index('cat > "$LAB_ETC/lab-run.env"')
assert 'cat "$tls_env" >> "$LAB_ETC/lab-run.env"' in bootstrap
print('runner TLS: CA paths, quoted filenames, rerun continuity and private-value exclusion passed')
