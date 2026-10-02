#!/usr/bin/env python3
"""Read only explicit CA file settings from the operator's lab-run environment."""
from pathlib import Path
import re
import shlex
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
    if len(sys.argv) != 2:
        print('usage: preserve-tls-env.py LAB_RUN_ENV', file=sys.stderr)
        return 2
    try:
        settings = preserve(Path(sys.argv[1]))
    except (OSError, ValueError) as error:
        print(f'CA environment: {error}', file=sys.stderr)
        return 1
    if settings:
        print('\n'.join(settings))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
