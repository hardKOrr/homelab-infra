# Gate toolchain

Run repository lint and test checks through the committed wrappers. They establish the WSL
environment, close stdin, select the correct change scope, and prevent shell-relay quoting from
silently turning a check into a no-op.

Invoked as:

```
lint: wsl bash -lc 'bash gate/lint.sh'
test: wsl bash -lc 'bash gate/test.sh'
```

Append `--all` to either to force the full sweep (see *Scope* below).

- `lint.sh` — `ansible-lint` profile `min` over `playbooks roles tasks vars`.
- `check-links.py` — validates repository-local Markdown links and repo-root `docs/*.md`
  references in tracked and untracked text files.
- `test.sh` — `ansible-playbook --syntax-check` over every playbook, with the Proxmox dynamic
  inventory neutralized (`ANSIBLE_INVENTORY=localhost,`) so no credentials are needed.
- `lib-scope.sh` — sourced by both; decides full sweep vs. changed-only.

## Scope

A full sweep starts a separate `ansible-playbook` process for every playbook and lints the whole
Ansible tree. Both gates therefore narrow by default to what the working tree changed. Ambiguous
scope falls back to the full sweep:

| Situation | Scope |
| --- | --- |
| `--all` argument, or `GATE_SCOPE=all` | full |
| Clean working tree | full |
| `git` unreadable (WSL can refuse a Windows checkout on dubious ownership) | full |
| `roles/ tasks/ vars/ inventory/ files/ ansible.cfg requirements.yml gate/` touched | full |
| Only Markdown touched, including Markdown inside an Ansible or gate directory | changed; skip Ansible lint and syntax checks |
| Only playbooks/docs touched | changed |

A clean tree resolving to *full* is the load-bearing rule: the run that gates a commit happens
after the commit, so there is no state in which "nothing changed" reports a green. A changed
playbook also drags in any playbook whose text names it, because `bootstrap.yml` chains the app
playbooks with `import_playbook`.

Markdown never promotes a changed run to full scope. `lint.sh` still compiles every Jinja
expression and runs `check-links.py`, and `test.sh` still runs every focused regression test.
The optimization skips only Ansible lint and playbook syntax checks that cannot consume a
Markdown file.

`test.sh` checks playbooks in parallel. Override its concurrency with `GATE_JOBS=n`.

Both export `ANSIBLE_CONFIG` to the checkout's absolute path derived from `$PWD`: the repo lives
on NTFS under `/mnt/c`, and Ansible's world-writable-cwd check silently ignores a cwd-relative
`ansible.cfg`.

## One-time bootstrap (fresh WSL distro)

Interactive sudo, run once:

```bash
sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
python3 -m venv ~/.venvs/homelab-ansible
~/.venvs/homelab-ansible/bin/pip install --upgrade pip
~/.venvs/homelab-ansible/bin/pip install -r gate/requirements-dev.txt
~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml
```
