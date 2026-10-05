#!/usr/bin/env python3
"""Exercise the source pct/root readiness boundary with disposable local HTTPS."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import re
import shlex
import shutil
import ssl
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'rundeck/bootstrap-rundeck.sh').read_text()
predicate = re.search(r'elif ! (.+); then\n    warn "https://vaultwarden', source)[1]
in_ct = re.search(r'^in_ct\(\).*$', source, re.M)[0]
enrollment = source.split('  VAULT_ENROLLED=0\n', 1)[1].split('\n  if [ "$VAULT_ENROLLED" = 1 ]; then', 1)[0]
enrollment = 'VAULT_ENROLLED=0\n' + enrollment


class Alive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/alive' else 503)
        self.end_headers()
        self.wfile.write(b'healthy')

    def log_message(self, *args):
        pass


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    cert = root / 'CA bundle.pem'
    other = root / 'other.pem'
    for target in (cert, other):
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(target.with_suffix('.key')), '-out', str(target),
                        '-days', '1', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                       check=True, capture_output=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Alive)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, cert.with_suffix('.key'))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # pct drops the node environment. Its root caller environment is independently
    # controlled here; the production predicate and in_ct definition run unchanged.
    pct = root / 'pct'
    pct.write_text('''#!/usr/bin/python3
import json, os, subprocess, sys
assert sys.argv[1:4] == ['exec', '999', '--']
if sys.argv[4] == 'sudo':
    assert sys.argv[5:8] == ['-u', 'rundeck', 'env']
    assert 'playbooks/maintenance/vaultwarden-enroll.yml' in sys.argv[-1]
    assert sys.stdin.read() == 'unchanged-fixture-token\\n'
    open(os.path.join(os.environ['FIXTURE_ROOT'], 'enrollment-called'), 'w').close()
    sys.exit(0)
env = {'PATH': '/usr/bin:/bin', 'HOME': os.environ['FIXTURE_ROOT']}
env.update(json.load(open(os.environ['CALLER_FILE'])))
sys.exit(subprocess.run(sys.argv[4:], env=env).returncode)
''')
    pct.chmod(0o755)
    environment = root / 'lab-run.env'
    caller = root / 'caller.json'
    credentials = root / 'credentials'
    credentials.write_bytes(b'RUNDECK_API_TOKEN=unchanged-fixture\n')
    url = f'https://127.0.0.1:{server.server_port}/alive'

    def run(declarations='', overrides=None, endpoint=url, absent=False, flow=False,
            expect_stderr=None):
        environment.write_text('TOKEN=not-exported\n' + declarations)
        if absent:
            environment.unlink()
        before = (environment.read_bytes() if environment.exists() else None,
                  credentials.read_bytes(), cert.read_bytes())
        caller.write_text(json.dumps(overrides or {}))
        # Supply the source URL as one literal shell argument, without changing curl.
        command = enrollment if flow else predicate
        command = command.replace('"https://vaultwarden.$LAB_DOMAIN/alive"', '"$FIXTURE_URL"')
        if flow:
            command = 'log() { :; }; warn() { :; }; info() { :; };\n' + command
            command += '\nexit "$((1 - VAULT_ENROLLED))"'
        env = {'PATH': f'{root}:/usr/bin:/bin', 'HOME': directory,
               'VMID': '999', 'REPO_DIR': str(ROOT), 'VENV_DIR': '/usr',
               'LAB_ETC': directory, 'FIXTURE_URL': endpoint,
               'FIXTURE_ROOT': directory, 'CALLER_FILE': str(caller),
               'VAULTWARDEN_OWNER_EMAIL': 'owner@example.test',
               'VAULTWARDEN_AUTOMATION_EMAIL': 'automation@example.test',
               'RD_TOKEN': 'unchanged-fixture-token',
               'SSL_CERT_FILE': str(cert), 'CURL_CA_BUNDLE': str(cert)}
        result = subprocess.run(['bash', '-c', in_ct + '\n' + command],
                                env=env, capture_output=True, text=True)
        assert before == (environment.read_bytes() if environment.exists() else None,
                          credentials.read_bytes(), cert.read_bytes())
        assert result.stdout == '', 'readiness response body must remain suppressed'
        assert 'not-exported' not in result.stderr and 'unchanged-fixture-token' not in result.stderr
        if expect_stderr is not None:
            assert expect_stderr in result.stderr, 'readiness failure diagnostic must remain visible'
        return result.returncode

    try:
        declared = ''.join(f'export {key}={shlex.quote(str(cert))}\n' for key in
                           ('SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS'))
        assert run() != 0, 'scrubbed pct must not inherit node trust'
        assert run(absent=True) != 0, 'absent declarations must not inherit node trust'
        assert run(declared) == 0, 'healthy declared-trust HTTPS must permit enrollment'
        assert run(declared, flow=True) == 0
        assert (root / 'enrollment-called').exists()
        (root / 'enrollment-called').unlink()
        assert run(flow=True) != 0
        assert not (root / 'enrollment-called').exists()
        assert run('CURL_CA_BUNDLE=/missing.pem\n', flow=True,
                   expect_stderr='CA environment: line 2: CURL_CA_BUNDLE must name an existing absolute CA file') != 0
        assert run(f'CURL_CA_BUNDLE={shlex.quote(str(other))}\n', flow=True,
                   expect_stderr='curl: (60)') != 0
        assert not (root / 'enrollment-called').exists()
        for key in ('SSL_CERT_FILE', 'CURL_CA_BUNDLE'):
            assert run(f'{key}={shlex.quote(str(cert))}\n') == 0
        assert run(declared, {'CURL_CA_BUNDLE': str(other)}) != 0, 'caller trust takes precedence'
        assert run(declared, {'CURL_CA_BUNDLE': str(cert)}) == 0
        assert run(declared, {'CURL_CA_BUNDLE': '/missing.pem'}) != 0
        assert run(declared, {'CURL_CA_BUNDLE': ''}) == 0, 'empty caller uses declaration'
        assert run(f'CURL_CA_BUNDLE={shlex.quote(str(other))}\n') != 0
        for invalid in ('CURL_CA_BUNDLE=relative.pem\n', 'CURL_CA_BUNDLE=/missing.pem\n',
                        f'CURL_CA_BUNDLE={shlex.quote(directory)}\n',
                        'CURL_CA_BUNDLE="unterminated\n', declared + declared):
            assert run(invalid) != 0, 'invalid declarations must stay pending'
        malformed = root / 'malformed.pem'
        malformed.write_text('not a certificate')
        assert run(f'CURL_CA_BUNDLE={malformed}\n') != 0
        literal = root / '$(touch forbidden); CA.pem'
        shutil.copyfile(cert, literal)
        assert run(f'CURL_CA_BUNDLE={shlex.quote(str(literal))}\nTOKEN=$(touch forbidden)\n') == 0
        assert not (root / 'forbidden').exists() and not (ROOT / 'forbidden').exists()
        assert run(declared, endpoint=url.replace('/alive', '/unhealthy')) != 0
        server.shutdown()
        server.server_close()
        assert run(declared) != 0, 'unreachable service must stay pending'
    finally:
        server.server_close()
        thread.join(timeout=3)

    # Record the actual curl child environment separately, without TLS transport:
    # only CA declarations cross the helper; unrelated file credentials stay private.
    curl = root / 'curl'
    curl.write_text('#!/usr/bin/python3\nimport json, os\n'
                    'json.dump(dict(os.environ), open(os.environ["PROBE_OUT"], "w"))\n')
    curl.chmod(0o755)
    environment.write_text(declared + 'TOKEN=private-fixture\nRUNDECK_API_TOKEN=private-fixture\n')
    probe = root / 'curl-env.json'
    result = subprocess.run(['/usr/bin/python3', str(ROOT / 'rundeck/preserve-tls-env.py'),
                             '--check-https', str(environment), url],
                            env={'PATH': f'{root}:/usr/bin:/bin', 'PROBE_OUT': str(probe)},
                            capture_output=True)
    assert result.returncode == 0
    seen = json.loads(probe.read_text())
    for key in ('SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS'):
        assert seen[key] == str(cert)
    assert 'TOKEN' not in seen and 'RUNDECK_API_TOKEN' not in seen

print('bootstrap readiness: source pct/root trust, validation, caller precedence, HTTPS failures and preservation passed')
