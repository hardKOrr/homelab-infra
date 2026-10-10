# Gate toolchain

Bootstrap once on Debian/Ubuntu (adjust the package manager for another distro):

```bash
sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
python3 -m venv ~/.venvs/homelab-ansible
~/.venvs/homelab-ansible/bin/pip install --upgrade pip
~/.venvs/homelab-ansible/bin/pip install -r gate/requirements-dev.txt
~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml
```

Add `~/.venvs/homelab-ansible/bin` to `PATH` so Python scripts called by the platform
can import the installed dependencies.

Run from the repository root:

```bash
bash gate/lint.sh
bash gate/test.sh
```

Both commands always check the applicable tree and accept and ignore arguments.
They close stdin and select a local inventory so no live Proxmox inventory is invoked.

Lint runs `ansible-lint` over `ansible/{playbooks,roles,tasks,vars}`, parses Jinja,
checks repository links and fixture secrets, and enforces GitHub workflow policy.

Test syntax-checks every playbook in parallel, replays diagnostics in order, and
runs eleven logic suites. Set `GATE_JOBS=n` to choose syntax-check concurrency.
The Python suites exercise production logic, expressions and templates.

- `test-allocate-ip.sh`: address allocation, exclusions and exhaustion.
- `test-vmid-from-ip.sh`: Bash/Jinja VMID agreement and valid VMID range.
- `test-config.py`: Config Doctor, precedence, estates, mail, provider gates and networks.
- `test-rundeck-yaml.py`: rendered jobs, YAML aliases, estate options and instance publishing.
- `test-template-rendering.py`: Caddy, Emby, Navidrome, Maintainerr restore PVCs and Mautic.
- `test-vaultwarden-login.py`: the vaulted Vaultwarden login round trip and `lab-run.sh` loading it.
- `test-actual-budget-password.py`: Actual Budget's native terminal password exchange and failure handling.
- `test-plex-recovery.py`: Plex native capability/dispatch and non-mutating restore preview.
- `test-servarr-recovery.py`: shared four-app dispatch, restore preview and API key continuity.
- `test-sabnzbd-clients.py`: authenticated category creation, unique client wiring, convergence and selected-client removal without changing siblings.
- `test-sabnzbd-restore.py`: authenticated recovery, read-only preview, group guard and rollback after extraction, move, copy, ownership, start, health or credential publication failures.
- `test-bazarr-restore.py`: authenticated recovery, read-only preview, group guard and rollback after copy, ownership, start, health or credential publication failures.
- `test-deemix-restore.py`: read-only preview, group guard and rollback after extraction, move, copy, ownership, start or health failures.
- `test-recovery-dispatch.py`: backup and restore inputs remain available to the Docker dispatch play.
- `test-proxmox-task.py`: guest backup/restore task IDs, exact polling paths and invalid-output guards.
- `test-decommission-retire.py`: PBS stamp retirement, credential provenance checks and repeat execution.
- `test-firewall-wiring.py`: OPNsense egress rules and opt-in port forwards against a fake API: create, rerun, drift, opt-out and scoped removal.

On a Windows checkout accessed through WSL, wrap each command:

```bash
wsl bash -lc 'bash gate/lint.sh'
wsl bash -lc 'bash gate/test.sh'
```

The Gate workflow runs lint and test on pull requests targeting master and pushes to master.
