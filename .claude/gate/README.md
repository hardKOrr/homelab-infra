# Gate toolchain

Lint and test gates for the Ansible repo. Both are thin wrappers around committed scripts
(`lint.sh`, `test.sh`) so the Windows→WSL relay never mangles quoting: a bare `$(...)`/`"$var"`
one-liner can mis-expand on the Windows side and silently run zero iterations while still exiting
0. `.gitattributes` forces `*.sh` here to LF so `bash` in WSL never chokes on a CRLF shebang.

Invoked as:

```
lint: wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'
test: wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/test.sh'
```

Those two exact strings are the only WSL commands in `.claude/settings.json` `allow:`, and that
is deliberate. **`wsl bash -lc` must never become an allow *prefix*.** The permission check reads
the single-quoted inner string as one literal argument, so a prefix rule would auto-approve any
chained inner command — `wsl bash -lc 'bash .claude/gate/lint.sh && anything'` — making the relay
a bare interpreter in disguise. If ad-hoc iteration (lint one file, syntax-check one playbook)
ever prompts often enough to hurt, add argv-form wrappers here instead: `wsl bash
.claude/gate/lint-file.sh <path>` with no inner shell string, so chaining characters stay
unquoted on the Windows side and fail the check rather than smuggle through. Any such wrapper
must replicate the env exports below and be forced to LF in `.gitattributes`.

Stale path note: `.isotope/isotope.json` still registers these gates under a `kevin/GitHub`
checkout path that does not exist. Isotope is not in use here — see the cleanup note in
`.claude/meta/INDEX.md`.

- `lint.sh` — `ansible-lint` profile `min` over `playbooks roles tasks vars`.
- `test.sh` — `ansible-playbook --syntax-check` over every playbook, with the Proxmox dynamic
  inventory neutralized (`ANSIBLE_INVENTORY=localhost,`) so no credentials are needed.

Both export `ANSIBLE_CONFIG` to the checkout's absolute path derived from `$PWD`: the repo lives
on NTFS under `/mnt/c`, and Ansible's world-writable-cwd check silently ignores a cwd-relative
`ansible.cfg`.

## One-time bootstrap (fresh WSL distro)

Interactive sudo, run once:

```bash
sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
python3 -m venv ~/.venvs/homelab-ansible
~/.venvs/homelab-ansible/bin/pip install --upgrade pip
~/.venvs/homelab-ansible/bin/pip install -r .claude/gate/requirements-dev.txt
~/.venvs/homelab-ansible/bin/ansible-galaxy collection install \
    community.proxmox:==2.0.0 ansible.utils:==6.0.3 community.general:==13.1.0 community.docker:==5.2.1
```

When `ansible/requirements.yml` is reconciled to carry the pins, switch the galaxy line to
`-r ansible/requirements.yml`.
