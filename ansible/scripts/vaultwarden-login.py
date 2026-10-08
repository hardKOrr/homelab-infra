#!/usr/bin/env python3
"""Read or write config/vaultwarden.yml, the vaulted automation login for direct runs.

    vaultwarden-login.py read  <file>   print shell exports for BW_CLIENTID, BW_CLIENTSECRET
                                        and BW_PASSWORD, for lab-run.sh to eval
    vaultwarden-login.py write <file>   store those three variables from the environment,
                                        each as an inline Ansible Vault value

The file is what lets lab-run.sh unlock Vaultwarden without Rundeck: an operator or agent
supplies only the Ansible Vault password, and the existing Vaultwarden preflight does the
rest. Vaultwarden stays the canonical store for every other secret.

The Ansible Vault password comes from, in order:

  1. LAB_VAULT_PASSWORD — a job runner's secure option, passed through the environment;
  2. Ansible's own sources: ANSIBLE_VAULT_IDENTITY_LIST (`label@prompt`, or
     `label@<file or executable>`) and ANSIBLE_VAULT_PASSWORD_FILE;
  3. a prompt on the controlling terminal, when there is one.

Secrets travel only through the environment, stdin and stdout. `read` prints nothing but
the export lines; `write` prints `unchanged` or `written`. A rotation keeps the previous
file under config/.backups/, like every other config write.
"""

import os
import shlex
import sys
import tempfile
import time

import yaml

FIELDS = (("bw_clientid", "BW_CLIENTID"),
          ("bw_clientsecret", "BW_CLIENTSECRET"),
          ("bw_password", "BW_PASSWORD"))
BACKUPS_KEPT = 20
HEADER = """\
# Vaultwarden automation login for direct lab-run.sh operation. Written by the
# Vaultwarden Login File job (playbooks/maintenance/vaultwarden-login-file.yml);
# each value is encrypted with the operator's Ansible Vault password.
"""


class LoginError(Exception):
    pass


def vault_secrets() -> list:
    # ansible.parsing.vault rather than ansible.cli: the CLI package refuses to import
    # unless stdout is blocking, and lab-run captures this script's stdout.
    from ansible import constants as C
    from ansible.errors import AnsibleError
    from ansible.parsing.dataloader import DataLoader
    from ansible.parsing.vault import PromptVaultSecret, VaultSecret, get_file_vault_secret

    password = os.environ.get("LAB_VAULT_PASSWORD", "")
    if password:
        return [("default", VaultSecret(password.encode()))]

    sources = []
    for identity in C.DEFAULT_VAULT_IDENTITY_LIST or []:
        label, _, source = identity.rpartition("@")
        sources.append((label or C.DEFAULT_VAULT_IDENTITY, source))
    if C.DEFAULT_VAULT_PASSWORD_FILE:
        sources.append((C.DEFAULT_VAULT_IDENTITY, C.DEFAULT_VAULT_PASSWORD_FILE))
    if not sources:
        try:
            with open("/dev/tty"):
                sources.append((C.DEFAULT_VAULT_IDENTITY, "prompt"))
        except OSError:
            raise LoginError(
                "no Ansible Vault password source. Set ANSIBLE_VAULT_PASSWORD_FILE or "
                "ANSIBLE_VAULT_IDENTITY_LIST=lab@prompt, or run from a terminal.") from None

    secrets = []
    loader = DataLoader()
    for label, source in sources:
        if source in ("prompt", "prompt_ask_vault_pass"):
            secret = PromptVaultSecret(vault_id=label, prompt_formats=["Vault password: "])
        else:
            secret = get_file_vault_secret(filename=source, vault_id=label, loader=loader)
        try:
            secret.load()
        except AnsibleError as error:
            raise LoginError(f"Ansible Vault password source {label}: {error}") from None
        secrets.append((label, secret))
    return secrets


class _VaultLoader(yaml.SafeLoader):
    pass


_VaultLoader.add_constructor("!vault", lambda loader, node: ("vault", loader.construct_scalar(node)))


def load(path: str, secrets: list) -> dict:
    from ansible.parsing.vault import VaultLib

    try:
        with open(path, encoding="utf-8") as handle:
            document = yaml.load(handle, Loader=_VaultLoader) or {}
    except (OSError, yaml.YAMLError) as error:
        raise LoginError(f"cannot read {path}: {error}") from None
    if not isinstance(document, dict):
        raise LoginError(f"{path} must be a mapping")
    vault = VaultLib(secrets)
    values = {}
    for key, _ in FIELDS:
        value = document.get(key)
        if not (isinstance(value, tuple) and value[0] == "vault"):
            raise LoginError(f"{path}: {key} must be an inline !vault value")
        try:
            values[key] = vault.decrypt(value[1].encode()).decode()
        except Exception:  # noqa: BLE001 — several Ansible error types; none may echo data
            raise LoginError(f"{path}: {key} could not be decrypted with the supplied "
                             "Ansible Vault password") from None
        if not values[key]:
            raise LoginError(f"{path}: {key} is empty")
    return values


def read(path: str) -> None:
    values = load(path, vault_secrets())
    for key, env in FIELDS:
        print(f"export {env}={shlex.quote(values[key])}")


def backup(path: str) -> None:
    directory = os.path.join(os.path.dirname(path), ".backups")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    name = os.path.basename(path)
    target = os.path.join(directory, f"{name}.{time.strftime('%Y%m%dT%H%M%S')}")
    with open(path, "rb") as source:
        data = source.read()
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    kept = sorted(entry for entry in os.listdir(directory) if entry.startswith(f"{name}."))
    for stale in kept[:-BACKUPS_KEPT]:
        os.unlink(os.path.join(directory, stale))


def write(path: str) -> None:
    from ansible.parsing.vault import VaultLib

    wanted = {key: os.environ.get(env, "") for key, env in FIELDS}
    missing = [env for key, env in FIELDS if not wanted[key]]
    if missing:
        raise LoginError("missing from the environment: " + ", ".join(missing))
    secrets = vault_secrets()

    # Reruns keep the existing file. One the current password cannot read is replaced:
    # the caller has just proved these inputs unlock Vaultwarden.
    if os.path.exists(path):
        try:
            if load(path, secrets) == wanted:
                print("unchanged")
                return
        except LoginError:
            pass
        backup(path)

    vault = VaultLib(secrets)
    secret = secrets[0][1]
    lines = [HEADER]
    for key, _ in FIELDS:
        ciphertext = vault.encrypt(wanted[key], secret).decode()
        lines.append(f"{key}: !vault |\n")
        lines.extend(f"  {line}\n" for line in ciphertext.splitlines())

    descriptor, temporary = tempfile.mkstemp(prefix=".vaultwarden.", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("".join(lines))
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise

    if load(path, secrets) != wanted:
        raise LoginError(f"{path} did not read back the values just written")
    print("written")


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("read", "write"):
        print("usage: vaultwarden-login.py read|write <file>", file=sys.stderr)
        return 2
    try:
        (read if sys.argv[1] == "read" else write)(sys.argv[2])
    except LoginError as error:
        print(f"vaultwarden-login: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
