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

Test syntax-checks every playbook, then runs every `gate/test-*.py` and `gate/test-*.sh`
suite. Both phases run in parallel and replay each item's diagnostics in order. Set
`GATE_JOBS=n` to choose the concurrency (default: the number of cores), and
`GATE_SHARD=k/n` to run only the k-th of n slices (shard 1 also syntax-checks); CI runs
three shards on separate runners. The suites exercise production logic, expressions and
templates.

- `test-allocate-ip.sh`: address allocation, exclusions and exhaustion.
- `test-vmid-from-ip.sh`: Bash/Jinja VMID agreement and valid VMID range.
- `test-config.py`: Config Doctor, precedence, estates, mail, provider gates and networks.
- `test-kometa-config.py`: optional operator-owned collection YAML stays in the Kubernetes Secret and mounts read-only only when configured.
- `test-database-facts.py`: generated database registry cleanup, PostgreSQL privilege/extension setup and delete/redeploy cluster lifecycle.
- `test-rundeck-yaml.py`: rendered jobs, YAML aliases, estate options and instance publishing.
- `test-template-rendering.py`: app templates and defaults, including Caddy, the media apps and the Kubernetes manifests.
- `test-vaultwarden-login.py`: the vaulted Vaultwarden login round trip and `lab-run.sh` loading it.
- `test-lab-refresh.py`: ordinary runs keep the shared checkout pinned across concurrent jobs and a remote push; explicit refresh still updates it.
- `test-actual-budget-password.py`: Actual Budget's native terminal password exchange and failure handling.
- `test-plex-recovery.py`: Plex native capability/dispatch and non-mutating restore preview.
- `test-ntfy-recovery.py`: native auth/cache archives, read-only planning, group guards and two-directory rollback.
- `test-influxdb-recovery.py`: native data archive, restore credential checks and transactional rollback after replacement failures.
- `test-redis-recovery.py`: default native LXC deploy, ACL password rotation, `nopass` convergence and `ACL SAVE` flag preservation, Redis readiness retries, native RDB PBS recovery, restore rollback and scoped guest removal.
- `test-postgresql-recovery.py`: native LXC capability, consistent physical PBS backup, manifest and version guards, restore health checks and rollback.
- `test-uptime-kuma-recovery.py`: quiesced PBS snapshots, archive and destination guards, monitor-data restore ordering and rollback.
- `test-servarr-recovery.py`: shared four-app dispatch, restore preview and API key continuity.
- `test-sabnzbd-clients.py`: authenticated category creation, unique client wiring, convergence and selected-client removal without changing siblings.
- `test-sabnzbd-restore.py`: authenticated recovery, read-only preview, group guard and rollback after extraction, move, copy, ownership, start, health or credential publication failures.
- `test-bazarr-restore.py`: authenticated recovery, read-only preview, group guard and rollback after copy, ownership, start, health or credential publication failures.
- `test-deemix-restore.py`: read-only preview, group guard and rollback after extraction, move, copy, ownership, start or health failures.
- `test-recovery-dispatch.py`: backup and restore inputs remain available across dispatch plays, and LiteLLM, Plane and Odoo Remove guard and delete only their validated, unshared named PostgreSQL state.
- `test-proxmox-task.py`: guest backup/restore task IDs, exact polling paths and invalid-output guards.
- `test-guest-placement.py`: database pinning, ordered floating node selection, class storage and placement refusal paths.
- `test-decommission-retire.py`: PBS stamp retirement, credential provenance checks and repeat execution.
- `test-orphan-stack-tag.py`: the orphan stack tag repair's plan, execute, repeat and refusal cases.
- `test-repair-missing-stack-tag.py`: the missing stack identity repair's plan, execute, repeat and refusal cases.
- `test-start-owned-guest.py`: exact owned stack guest start, readiness, repeat and refusal cases.
- `test-orphan-postgresql-tag.py`: exact owned-guest selection, plan, tag-only repair, repeat and refusal cases.
- `test-qbittorrent-clients.py`: unique client wiring, convergence and selected-client removal.
- `test-plex-restore.py`, `test-tautulli-restore.py`, `test-qbittorrent-restore.py`: read-only preview, group guard, restore and rollback.
- `test-observability-recovery.py`: both-service quiescence, complete data archive, read-only preview, point/group guards and rollback after replacement failures.
- `test-karakeep-recovery.py`: one-archive backup of both persistent volumes and restore
  rollback after either original directory moves.
- `test-bookstack-recovery.py`: BookStack validates its default admin address, ensures
  its backup scheduler, selects the matching MariaDB client command, Remove clears only
  its validated, unshared database and login, and Restore separates read-only plan
  results from overwrite results, quotes table identifiers and disables foreign-key
  checks only while dropping that database's tables, requires a readable safety snapshot
  no more than one hour old, rejects future and malformed snapshot times, and returns the
  original data and database after an import failure.
- `test-wordpress-recovery.py`: WordPress Remove guards its unshared named database and
  login, Restore checks a recent readable safety point and the login endpoint, and a failed
  replacement restores the original `wp-content` tree and database. It also checks that
  the bind-mounted data directory is accessible to Apache's `www-data` user.
- `test-firewall-wiring.py`: OPNsense egress rules and opt-in port forwards against a fake API: create, rerun, drift, opt-out and scoped removal.

On a Windows checkout accessed through WSL, wrap each command:

```bash
wsl bash -lc 'bash gate/lint.sh'
wsl bash -lc 'bash gate/test.sh'
```

The Gate workflow runs lint and test on pull requests targeting master and pushes to master.
