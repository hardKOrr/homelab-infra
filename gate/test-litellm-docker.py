#!/usr/bin/env python3
"""LiteLLM on Docker renders its routing config, but keeps the restored one on restore."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESTORED = "model_list:\n  - model_name: source-route\ngeneral_settings:\n  master_key: x\n"


def run(scratch, *extra):
    return subprocess.run(
        [os.environ.get("GATE_ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook"),
         "-i", str(ROOT / "gate/fixtures/localhost.ini"),
         str(ROOT / "gate/fixtures/litellm-config.yml"),
         "-e", f"scratch={scratch}", *extra],
        cwd=ROOT, env={**os.environ, "ANSIBLE_ROLES_PATH": str(ROOT / "ansible/roles")}, text=True, capture_output=True, check=False)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config.yaml"
        # A normal deploy renders the instance's routes over whatever is there.
        config.write_text(RESTORED, encoding="utf-8")
        result = run(tmp)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "target-route" in config.read_text(encoding="utf-8")

        # A restore reconciliation keeps a snapshot whose routes differ from the target's.
        config.write_text(RESTORED, encoding="utf-8")
        result = run(tmp, "-e", "recovery_restore_config=true")
        assert result.returncode == 0, result.stdout + result.stderr
        assert config.read_text(encoding="utf-8") == RESTORED

        # ...and refuses a missing or invalid one.
        config.write_text("not: [a, valid", encoding="utf-8")
        assert run(tmp, "-e", "recovery_restore_config=true").returncode != 0
        config.unlink()
        assert run(tmp, "-e", "recovery_restore_config=true").returncode != 0
    print("LiteLLM on Docker keeps a restored routing config during restore reconciliation")


if __name__ == "__main__":
    main()
