#!/usr/bin/env python3
"""The vaulted Vaultwarden login: helper round trip and lab-run.sh loading it."""

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
helper = repo / "ansible" / "scripts" / "vaultwarden-login.py"
LOGIN = {
    "BW_CLIENTID": "user.0f1e2d3c",
    "BW_CLIENTSECRET": "s3cr'et $HOME `x`",
    "BW_PASSWORD": 'pass word "quoted"',
}


def run(args, env, stdin=""):
    base = {key: value for key, value in os.environ.items()
            if not key.startswith(("BW_", "ANSIBLE_VAULT", "LAB_"))}
    return subprocess.run(args, env=base | env, input=stdin, text=True, capture_output=True,
                          start_new_session=True, check=False)


def check_helper(work: Path) -> None:
    login = work / "vaultwarden.yml"
    password = work / "vault-password"
    password.write_text("correct horse\n", encoding="utf-8")
    write = [sys.executable, str(helper), "write", str(login)]
    read = [sys.executable, str(helper), "read", str(login)]

    first = run(write, LOGIN | {"LAB_VAULT_PASSWORD": "correct horse"})
    assert first.returncode == 0 and first.stdout.strip() == "written", first.stderr
    assert stat.S_IMODE(login.stat().st_mode) == 0o600, "login file must be 0600"
    text = login.read_text(encoding="utf-8")
    assert text.count("!vault |") == 3, "every value must be an inline !vault value"
    assert not any(value in text for value in LOGIN.values()), "plaintext in login file"

    again = run(write, LOGIN | {"ANSIBLE_VAULT_PASSWORD_FILE": str(password)})
    assert again.stdout.strip() == "unchanged", "an identical rerun must not rewrite"
    assert not (work / ".backups").exists(), "an identical rerun must not back up"

    for source in ({"ANSIBLE_VAULT_PASSWORD_FILE": str(password)},
                   {"ANSIBLE_VAULT_IDENTITY_LIST": f"lab@{password}"}):
        exported = run(read, source)
        assert exported.returncode == 0, exported.stderr
        echoed = run(["bash", "-c", exported.stdout
                      + 'printf "%s\\n%s\\n%s" "$BW_CLIENTID" "$BW_CLIENTSECRET" "$BW_PASSWORD"'], {})
        assert echoed.stdout.split("\n") == list(LOGIN.values()), "shell round trip changed a value"

    piped = run(read, {"ANSIBLE_VAULT_PASSWORD_FILE": "/dev/stdin"}, stdin="correct horse\n")
    assert piped.returncode == 0 and piped.stdout.count("export ") == 3, piped.stderr

    wrong = run(read, {"LAB_VAULT_PASSWORD": "wrong"})
    assert wrong.returncode == 1 and wrong.stdout == "", "a wrong password must fail closed"
    assert not any(value in wrong.stderr for value in LOGIN.values()), "secret in an error"

    unset = run(read, {})
    assert unset.returncode == 1 and "no Ansible Vault password source" in unset.stderr

    rotated = run(write, LOGIN | {"BW_PASSWORD": "rotated", "LAB_VAULT_PASSWORD": "correct horse"})
    assert rotated.stdout.strip() == "written", rotated.stderr
    assert len(list((work / ".backups").glob("vaultwarden.yml.*"))) == 1, "rotation keeps one backup"


def check_lab_run(work: Path) -> None:
    """lab-run must fill the missing login from the file and consume the password source."""
    lab_repo = work / "lab"
    (lab_repo / "config").mkdir(parents=True)
    (lab_repo / "ansible").symlink_to(repo / "ansible")
    state = work / "state"
    state.mkdir()
    (state / "vault-mode").write_text("version=1\n", encoding="utf-8")
    password = work / "vault-password"
    password.write_text("correct horse\n", encoding="utf-8")
    seeded = run([sys.executable, str(helper), "write", str(lab_repo / "config" / "vaultwarden.yml")],
                 LOGIN | {"LAB_VAULT_PASSWORD": "correct horse"})
    assert seeded.returncode == 0, seeded.stderr

    # A stub `bw` records what the preflight would log in with, then refuses the login,
    # so lab-run stops inside the real preflight before any playbook could start.
    stub = work / "bin"
    stub.mkdir()
    record = work / "bw-env"
    (stub / "bw").write_text(
        "#!/bin/bash\n"
        '[ "$1" = login ] || exit 0\n'
        f'printf "%s\\n" "$BW_CLIENTID" "$BW_CLIENTSECRET" "$BW_PASSWORD" '
        f'"${{ANSIBLE_VAULT_PASSWORD_FILE:-unset}}" > "{record}"\n'
        "exit 1\n", encoding="utf-8")
    (stub / "bw").chmod(0o755)
    venv = Path(sys.executable).parent.parent
    env = {
        "PATH": f"{stub}:{os.environ['PATH']}",
        "HOME": str(work),
        "LAB_ENV_FILE": str(work / "absent.env"),
        "LAB_REPO": str(lab_repo),
        "LAB_VENV": str(venv),
        "LAB_STATE_DIR": str(state),
        "LAB_REFRESH": "0",
        "BW_SERVER": "https://127.0.0.1:9",
        "BW_CLIENTID": "from-the-job",
        "ANSIBLE_VAULT_PASSWORD_FILE": str(password),
    }
    result = run(["bash", str(repo / "ansible" / "scripts" / "lab-run.sh"),
                  "playbooks/maintenance/status.yml"], env)
    assert result.returncode != 0, "the stub login must stop the run"
    assert "Vaultwarden login loaded" in result.stdout, result.stdout + result.stderr
    seen = record.read_text(encoding="utf-8").split("\n")[:4]
    assert seen == ["from-the-job", LOGIN["BW_CLIENTSECRET"], LOGIN["BW_PASSWORD"], "unset"], (
        "lab-run must keep a supplied value, fill the rest from the file and unset the"
        " password source")
    assert not any(value in result.stdout + result.stderr for value in LOGIN.values()), \
        "lab-run printed a secret"

    password.write_text("wrong\n", encoding="utf-8")
    record.unlink()
    refused = run(["bash", str(repo / "ansible" / "scripts" / "lab-run.sh"),
                   "playbooks/maintenance/status.yml"], env | {"BW_CLIENTID": ""})
    assert refused.returncode != 0 and "could not read the Vaultwarden login" in refused.stderr
    assert not record.exists(), "an undecryptable login file must stop before the preflight"


def main() -> int:
    failures = 0
    for check in (check_helper, check_lab_run):
        with tempfile.TemporaryDirectory(prefix="homelab-vw-login.") as directory:
            try:
                check(Path(directory))
                print(f"ok   {check.__name__}")
            except AssertionError as error:
                failures += 1
                print(f"FAIL {check.__name__}: {error}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
