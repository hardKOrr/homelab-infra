# Gate toolchain

Lint and test gates for the Ansible repo. Both are thin wrappers around committed scripts
(`lint.sh`, `test.sh`) so the Windows→WSL relay never mangles quoting: a bare `$(...)`/`"$var"`
one-liner can mis-expand on the Windows side and silently run zero iterations while still exiting
0. `.gitattributes` forces `*.sh` here to LF so `bash` in WSL never chokes on a CRLF shebang.

Invoked as:

```
lint: wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash gate/lint.sh'
test: wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash gate/test.sh'
```

Append `--all` to either to force the full sweep (see *Scope* below).

Those two exact strings are the only WSL commands in `.claude/settings.json` `allow:`, and that
is deliberate. **`wsl bash -lc` must never become an allow *prefix*.** The permission check reads
the single-quoted inner string as one literal argument, so a prefix rule would auto-approve any
chained inner command — `wsl bash -lc 'bash gate/lint.sh && anything'` — making the relay
a bare interpreter in disguise. If ad-hoc iteration (lint one file, syntax-check one playbook)
ever prompts often enough to hurt, add argv-form wrappers here instead: `wsl bash
gate/lint-file.sh <path>` with no inner shell string, so chaining characters stay
unquoted on the Windows side and fail the check rather than smuggle through. Any such wrapper
must replicate the env exports below and be forced to LF in `.gitattributes`.

- `lint.sh` — `ansible-lint` profile `min` over `playbooks roles tasks vars`.
- `test.sh` — `ansible-playbook --syntax-check` over every playbook, with the Proxmox dynamic
  inventory neutralized (`ANSIBLE_INVENTORY=localhost,`) so no credentials are needed.
- `lib-scope.sh` — sourced by both; decides full sweep vs. changed-only.

## Scope

A full sweep is 33 cold `ansible-playbook` interpreters plus a whole-tree lint, and on `/mnt/c`
that is minutes of 9p syscalls re-proving files nobody touched. Both gates therefore narrow by
default to what the working tree actually changed. The narrowing is deliberately biased toward
running too much:

| Situation | Scope |
| --- | --- |
| `--all` argument, or `GATE_SCOPE=all` | full |
| Clean working tree | full |
| `git` unreadable (WSL can refuse a Windows checkout on dubious ownership) | full |
| `roles/ tasks/ vars/ inventory/ files/ ansible.cfg requirements.yml gate/` touched | full |
| Only playbooks/docs touched | changed |

A clean tree resolving to *full* is the load-bearing rule: the run that gates a commit happens
after the commit, so there is no state in which "nothing changed" reports a green. A changed
playbook also drags in any playbook whose text names it, because `bootstrap.yml` chains the app
playbooks with `import_playbook`.

`test.sh` runs the checks under `xargs -P $(nproc)` — they are independent, and the wall clock is
interpreter-startup-bound rather than work-bound. Override with `GATE_JOBS=n`. Each check's output
goes to its own file and is replayed in playbook order afterwards, because interleaved writes from
parallel children would destroy the one thing a failing check exists to produce. Set
`GATE_ANSIBLE_PLAYBOOK` to a stub to exercise the runner itself without paying for real
interpreters.

Unrelated to the gates but on the same bottleneck: Microsoft Defender inspects every file WSL
reads over 9p. Excluding the checkout and the WSL VHDX is usually a larger win than anything in
these scripts.

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
~/.venvs/homelab-ansible/bin/ansible-galaxy collection install \
    community.proxmox:==2.0.0 ansible.utils:==6.0.3 community.general:==13.1.0 community.docker:==5.2.1
```

When `ansible/requirements.yml` is reconciled to carry the pins, switch the galaxy line to
`-r ansible/requirements.yml`.
