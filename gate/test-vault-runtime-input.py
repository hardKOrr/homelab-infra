#!/usr/bin/env python3
"""Keep vault-runtime's direct and opaque stdin modes aligned."""

import base64
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "ansible/scripts/vault-runtime.py"


def main():
    items = [{
        "name": "homelab-infra/apps/canary",
        "fields": [{"name": "app_key", "value": "fixture-app-key"}],
    }]
    source = json.dumps(items)
    direct = subprocess.run(
        [sys.executable, str(RUNTIME)], input=source, text=True,
        capture_output=True, check=False,
    )
    encoded = subprocess.run(
        [sys.executable, str(RUNTIME), "--base64-stdin"],
        input=base64.b64encode(source.encode()).decode(), text=True,
        capture_output=True, check=False,
    )
    assert direct.returncode == 0, direct.stderr
    assert encoded.returncode == 0, encoded.stderr
    assert json.loads(encoded.stdout) == json.loads(direct.stdout)

    invalid = subprocess.run(
        [sys.executable, str(RUNTIME), "--base64-stdin"],
        input="not-base64%%%", text=True, capture_output=True, check=False,
    )
    assert invalid.returncode == 2
    assert "Bitwarden item input must be valid base64" in invalid.stderr
    assert "not-base64" not in invalid.stderr
    print("Vault runtime stdin input checks passed")


if __name__ == "__main__":
    main()
