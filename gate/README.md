# Gate toolchain

Run repository lint and test checks through the committed wrappers. They close stdin, select
the correct change scope, and prevent shell-relay quoting from silently turning a check into a
no-op.

Invoked as:

```
lint: bash gate/lint.sh
test: bash gate/test.sh
```

Append `--all` to either to force the full sweep (see *Scope* below).

On a Windows checkout accessed through WSL, prefix each command with `wsl bash -lc '...'`, e.g.
`wsl bash -lc 'bash gate/lint.sh'`. A native Linux checkout needs no such prefix.

- `lint.sh` — `ansible-lint` profile `min` over `playbooks roles tasks vars`.
- `check-links.py` — validates repository-local Markdown links and repo-root `docs/*.md`
  references in tracked and untracked text files.
- `check-output-anchors.py` — links operator-facing output to the Markdown passage it
  restates. See *Output anchors* below.
- `test.sh` — `ansible-playbook --syntax-check` over every playbook, with the Proxmox dynamic
  inventory neutralized (`ANSIBLE_INVENTORY=localhost,`) so no credentials are needed.
- `lib-scope.sh` — sourced by both; decides full sweep vs. changed-only.

## Output anchors

Some text the platform prints to an operator restates a passage a `README.md` owns — the
bootstrap script's NETWORK and NEXT sections, the Get Config archive warning, the Reimport
Jobs description. A documentation pass can correct the contract and leave the printed text
stating the superseded thing, which `check-links.py` cannot see.

Declare the canonical passage in Markdown:

```
<!-- output-source:network-prerequisite sha=7cfe6710 -->
...the passage...
<!-- /output-source:network-prerequisite -->
```

Name the same id in a comment beside every place that restates it:

```
# Restates output-source:network-prerequisite, canonical in README.md.
```

`check-output-anchors.py` enforces the link, not textual equality — printed text
interpolates live values and wraps to a console. It fails when an anchor names no declared
passage, a passage has no consumer left, an id is declared twice, or a passage's content no
longer hashes to its declared `sha`.

The hash is the update chain. Change the canonical passage and the gate fails, naming every
output that restates it. Re-read each one, correct it, then record the new hash:

```
python3 gate/check-output-anchors.py --update
```

Use a new id when new output starts restating a documented passage. Do not run `--update`
to clear a failure you have not read.

## Scope

A full sweep starts a separate `ansible-playbook` process for every playbook and lints the whole
Ansible tree. Both gates therefore narrow by default to what the working tree changed. Ambiguous
scope falls back to the full sweep:

| Situation | Scope |
| --- | --- |
| `--all` argument, or `GATE_SCOPE=all` | full |
| Clean working tree | full |
| `git` unreadable (e.g. a checkout git refuses on dubious ownership) | full |
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

Both export `ANSIBLE_CONFIG` to the checkout's absolute path derived from `$PWD`: on a checkout
Ansible's world-writable-cwd check flags (e.g. NTFS under `/mnt/c` on a WSL checkout), it
silently ignores a cwd-relative `ansible.cfg` otherwise.

## One-time bootstrap (fresh machine)

Interactive sudo, run once (Debian/Ubuntu; adjust the package manager for another
distro):

```bash
sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
python3 -m venv ~/.venvs/homelab-ansible
~/.venvs/homelab-ansible/bin/pip install --upgrade pip
~/.venvs/homelab-ansible/bin/pip install -r gate/requirements-dev.txt
~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml
```
