#!/usr/bin/env python3
"""The vaulted Vaultwarden login: helper round trip, lab-run.sh loading it, edge copy.

Checks: the vaulted Vaultwarden login round trip and `lab-run.sh` loading it.
"""

import json
import os
import shutil
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


def check_login_rate_limit(work: Path) -> None:
    """A 429 from overlapping runs is retried and never reported as a bad credential."""
    lab_repo = work / "lab"
    (lab_repo / "config").mkdir(parents=True)
    (lab_repo / "ansible").symlink_to(repo / "ansible")
    state = work / "state"
    state.mkdir()
    (state / "vault-mode").write_text("version=1\n", encoding="utf-8")
    # The stub rate-limits the first `limited` logins, then accepts; its unlock returns an
    # empty session, so a run that gets past login stops at the next preflight step.
    stub = work / "bin"
    stub.mkdir()
    calls = work / "logins"
    limited = work / "limited"
    (stub / "bw").write_text(
        "#!/bin/bash\n"
        '[ "$1" = login ] || exit 0\n'
        f'echo x >> "{calls}"\n'
        f'if [ "$(wc -l < "{calls}")" -le "$(cat "{limited}")" ]; then\n'
        '  echo "Rate limit exceeded. Try again later." >&2; exit 1\n'
        "fi\n", encoding="utf-8")
    (stub / "bw").chmod(0o755)
    env = {
        "PATH": f"{stub}:{os.environ['PATH']}",
        "HOME": str(work),
        "LAB_ENV_FILE": str(work / "absent.env"),
        "LAB_REPO": str(lab_repo),
        "LAB_VENV": str(Path(sys.executable).parent.parent),
        "LAB_STATE_DIR": str(state),
        "LAB_REFRESH": "0",
        "LAB_VAULT_LOGIN_RETRY_DELAY": "0",
        "BW_SERVER": "https://127.0.0.1:9",
    } | LOGIN
    script = ["bash", str(repo / "ansible" / "scripts" / "lab-run.sh"),
              "playbooks/maintenance/status.yml"]

    limited.write_text("2", encoding="utf-8")
    recovered = run(script, env)
    assert calls.read_text().count("x") == 3, "two rate-limited logins must be retried"
    assert "rate-limited the login (attempt 2 of 5)" in recovered.stderr, recovered.stderr
    assert "empty vault session" in recovered.stderr, recovered.stderr
    assert "credential problem" not in recovered.stderr, "a 429 is not a bad credential"

    calls.unlink()
    limited.write_text("99", encoding="utf-8")
    exhausted = run(script, env)
    assert exhausted.returncode != 0 and calls.read_text().count("x") == 5
    assert "kept rate-limiting" in exhausted.stderr, exhausted.stderr
    assert "credential problem" not in exhausted.stderr, "a 429 is not a bad credential"


CONTRACT = {
    "proxmox": {"api_token_secret": "pve-token-5e"},
    "runner": {"ssh_private_key": "-----BEGIN KEY-----\nk3y\n-----END KEY-----"},
    "reverse_proxy": {"dns_api_token": "dns-7a"},
    "apps": {"forgejo": {"admin_password": "app-secret-1"}},
    "media": {"plex": {"token": "app-secret-2"}},
}


def check_edge_copy(work: Path) -> None:
    """The login file keeps an edge copy, and only a Caddy run whose vault is down uses it."""
    lab_repo = work / "lab"
    (lab_repo / "config").mkdir(parents=True)
    # A copy, not a symlink: the Proxmox wrapper reads ../config/proxmox.yml from ansible/,
    # and the kernel resolves that `..` through a symlink to this repository instead.
    shutil.copytree(repo / "ansible", lab_repo / "ansible", symlinks=True)
    login = lab_repo / "config" / "vaultwarden.yml"
    (lab_repo / "config" / "proxmox.yml").write_text(
        "proxmox:\n  api_host: 127.0.0.1\n  api_token_id: lab\n", encoding="utf-8")
    state = work / "state"
    state.mkdir()
    (state / "vault-mode").write_text("version=1\n", encoding="utf-8")
    vault = {"LAB_VAULT_PASSWORD": "correct horse"}
    write = [sys.executable, str(helper), "write", str(login)]
    read = [sys.executable, str(helper), "read", str(login)]

    first = run(write, LOGIN | vault | {"HOMELABINFRA_VAULT_JSON": json.dumps(CONTRACT)})
    assert first.returncode == 0 and first.stdout.strip() == "written", first.stderr
    text = login.read_text(encoding="utf-8")
    assert text.count("!vault |") == 4, "the edge copy must be a fourth inline !vault value"
    assert "pve-token" not in text, "plaintext edge copy in the login file"
    exported = run(read, vault)
    echoed = run(["bash", "-c", exported.stdout + 'printf "%s" "$HOMELABINFRA_EDGE_VAULT_JSON"'], {})
    edge = json.loads(echoed.stdout)
    assert set(edge) == {"proxmox", "runner", "reverse_proxy"}, "edge copy must drop app items"

    kept = run(write, LOGIN | vault)
    assert kept.stdout.strip() == "unchanged", "a write without a contract must keep the copy"
    no_runner = run(write, LOGIN | vault | {"HOMELABINFRA_VAULT_JSON": json.dumps({"proxmox": {"a": "b"}})})
    assert no_runner.returncode == 1 and "no runner item" in no_runner.stderr

    # bw refuses every login and curl cannot reach /alive: Caddy, and Vaultwarden behind
    # it, are down. A stub ansible-playbook records what the run would have received.
    stub = work / "bin"
    stub.mkdir()
    (stub / "bw").write_text("#!/bin/bash\n[ \"$1\" = login ] || exit 0\nexit 1\n", encoding="utf-8")
    (stub / "curl").write_text("#!/bin/bash\nexit 7\n", encoding="utf-8")
    venv = work / "venv"
    (venv / "bin").mkdir(parents=True)
    # A wrapper, not a symlink: a symlinked venv interpreter loses its venv.
    (venv / "bin" / "python3").write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n',
                                          encoding="utf-8")
    record = work / "playbook-env"
    (venv / "bin" / "ansible-playbook").write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$PROXMOX_API_TOKEN_SECRET" "$HOMELABINFRA_VAULT_JSON" > "{record}"\n',
        encoding="utf-8")
    for tool in (stub / "bw", stub / "curl", venv / "bin" / "ansible-playbook",
                 venv / "bin" / "python3"):
        tool.chmod(0o755)
    env = vault | {
        "PATH": f"{stub}:{os.environ['PATH']}",
        "HOME": str(work),
        "LAB_ENV_FILE": str(work / "absent.env"),
        "LAB_REPO": str(lab_repo),
        "LAB_VENV": str(venv),
        "LAB_STATE_DIR": str(state),
        "LAB_REFRESH": "0",
        "LAB_DOCTOR": "0",
        "BW_SERVER": "https://127.0.0.1:9",
    }
    lab_run = ["bash", str(repo / "ansible" / "scripts" / "lab-run.sh")]

    for args in (["playbooks/apps/caddy.yml", "-e", "instance=caddy"],
                 ["playbooks/apps/remove.yml", "-e", "instance=edge", "-e", "app=caddy"]):
        if record.exists():
            record.unlink()
        result = run(lab_run + args, env)
        output = result.stdout + result.stderr
        assert "Vaultwarden edge copy loaded" in result.stdout, output
        assert "did not answer. Vault mode is fail-closed" not in output, output
        assert not any(v in output for v in ("pve-token-5e", "dns-7a")), "lab-run printed a secret"
        assert record.exists(), "an edge run must reach ansible-playbook: " + output
        seen = record.read_text(encoding="utf-8").split("\n")
        assert seen[0] == "pve-token-5e", "the edge copy must supply the Proxmox token"
        assert "app-secret" not in seen[1], "an edge run must not see app credentials"

    for args in (["playbooks/maintenance/status.yml"],
                 ["playbooks/apps/remove.yml", "-e", "instance=radarr"]):
        refused = run(lab_run + args, env)
        assert refused.returncode != 0, args
        assert "did not answer. Vault mode is fail-closed" in refused.stderr, refused.stderr
        assert "edge copy" not in refused.stdout, "only a Caddy run may use the edge copy"

    # Vaultwarden answers and serves a rotated Proxmox token: the Caddy run refreshes the copy.
    items = [{"name": f"homelab-infra/{name}",
              "fields": [{"name": key, "value": value} for key, value in fields.items()]}
             for name, fields in (("proxmox", {"api_token_secret": "pve-rotated-6f"}),
                                  ("runner", CONTRACT["runner"]),
                                  ("vaultwarden", {"admin_token": "vw-admin"}))]
    (work / "items.json").write_text(json.dumps(items), encoding="utf-8")
    (stub / "bw").write_text(
        "#!/bin/bash\n"
        'case "$1" in unlock) echo session ;; list) cat "' + str(work / "items.json") + '" ;; esac\n',
        encoding="utf-8")
    (stub / "curl").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    refreshed = run(lab_run + ["playbooks/apps/caddy.yml", "-e", "instance=caddy"], env)
    assert "Vaultwarden edge copy refreshed" in refreshed.stdout, refreshed.stdout + refreshed.stderr
    exported = run(read, vault)
    assert "pve-rotated-6f" in exported.stdout, "the refresh must store the rotated token"

    # A direct Login File run loads the login from the file and still hands the playbook
    # the LAB_VAULT_PASSWORD value it needs to rewrite that file.
    (venv / "bin" / "ansible-playbook").write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "${{LAB_VAULT_PASSWORD:-unset}}" > "{record}"\n', encoding="utf-8")
    record.unlink()
    direct = run(lab_run + ["playbooks/maintenance/vaultwarden-login-file.yml"], env)
    assert "Vaultwarden login loaded" in direct.stdout, direct.stdout + direct.stderr
    assert record.read_text(encoding="utf-8").strip() == "correct horse", \
        "the Login File playbook must receive LAB_VAULT_PASSWORD"
    record.unlink()
    other = run(lab_run + ["playbooks/maintenance/status.yml"], env)
    assert other.returncode == 0, other.stdout + other.stderr
    assert record.read_text(encoding="utf-8").strip() == "unset", \
        "other playbooks must not receive LAB_VAULT_PASSWORD"
    (stub / "bw").write_text("#!/bin/bash\n[ \"$1\" = login ] || exit 0\nexit 1\n", encoding="utf-8")

    # Vaultwarden answers but refuses the login: a credential problem, even for Caddy.
    (stub / "curl").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    answered = run(lab_run + ["playbooks/apps/caddy.yml", "-e", "instance=caddy"], env)
    assert answered.returncode != 0 and "credential problem" in answered.stderr, answered.stderr


def main() -> int:
    failures = 0
    for check in (check_helper, check_lab_run, check_login_rate_limit, check_edge_copy):
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
