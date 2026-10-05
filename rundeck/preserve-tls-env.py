#!/usr/bin/env python3
"""Read only explicit CA file settings from the operator's lab-run environment."""
from pathlib import Path
import os
import re
import shlex
import subprocess
import sys

KEYS = {'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS'}


def preserve(path: Path) -> list[str]:
    if not path.exists():
        return []
    result = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        match = re.match(r'^\s*(?:export\s+)?([A-Z_]+)=', line)
        if not match or match.group(1) not in KEYS:
            continue
        parts = shlex.split(line, comments=True)
        if parts and parts[0] == 'export':
            parts.pop(0)
        if len(parts) != 1:
            raise ValueError(f'line {number}: CA setting must be one literal assignment')
        key, value = parts[0].split('=', 1)
        if key in result:
            raise ValueError(f'line {number}: duplicate {key}')
        if not Path(value).is_absolute() or not Path(value).is_file():
            raise ValueError(f'line {number}: {key} must name an existing absolute CA file')
        result[key] = f'{key}={shlex.quote(value)}'
    return list(result.values())


def main() -> int:
    readiness = len(sys.argv) == 4 and sys.argv[1] == '--check-https'
    if len(sys.argv) != 2 and not readiness:
        print('usage: preserve-tls-env.py [--check-https] LAB_RUN_ENV [HTTPS_URL]', file=sys.stderr)
        return 2
    try:
        settings = preserve(Path(sys.argv[2] if readiness else sys.argv[1]))
        if readiness:
            if not sys.argv[3].startswith('https://'):
                raise ValueError('readiness requires an HTTPS URL')
            # Run inside the target pct/root context: paths and caller settings are
            # container-local. Never source the environment file or export secrets.
            environment = os.environ.copy()
            for setting in settings:
                key, value = shlex.split(setting)[0].split('=', 1)
                if not environment.get(key):
                    environment[key] = value
            return subprocess.run(['curl', '-fsS', '--max-time', '15', sys.argv[3]],
                                  env=environment).returncode
    except (OSError, ValueError) as error:
        print(f'CA environment: {error}', file=sys.stderr)
        return 1
    if settings:
        print('\n'.join(settings))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
