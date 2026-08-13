# 601 — notes

## 2026-07-25 — implementation

`rundeck/jobs/*.yaml` — 14 job files, one per Semaphore template, same names and the
same one-click shape. `rundeck/README.md` rewritten around them. Every file parses as
YAML and every embedded script step passes `bash -n`.

### Decisions

**One job per app**, matching slice 600 — `instance=<app>` is baked into the step, so
there is nothing to type. UUIDs are `uuid5` of the job name, so `rd jobs load` updates
in place instead of duplicating on re-import.

**Inline `script:` steps, not the Ansible plugin.** A script step is one readable unit
that any operator can run by hand to debug, it needs no plugin configuration, and it
runs from `ansible/` so the repo's `ansible.cfg` (and its relative `roles_path`) applies
without the `ANSIBLE_ROLES_PATH` override Semaphore needs.

**Options arrive as `RD_OPTION_*` environment variables.** Rundeck's `${option.x}`
tokens are indistinguishable from shell parameter expansion inside a script body, and
the `${option.x@!blank@ …}` conditional form is an argument-line feature. Reading the
environment sidesteps both. Optional options use `if [ -n … ]; then … fi` rather than
`[ -n … ] && …`, which would exit the whole script under `set -e` when the test fails.

**Proxmox credentials come from `config/proxmox.yml`, not Key Storage.** Every step
wraps its invocation in `scripts/with-proxmox-env.sh`. This removes Key Storage from the
critical path entirely: fill in the same two config files the CLI path uses and every
job runs.

**`with-proxmox-env.sh` was extended to read `config/proxmox.yml`.** It only understood
the legacy `homelabinfra_config: {proxmox: …}` wrapper, so pointing it at the current
config model's top-level `proxmox:` block failed. It now accepts either shape, preferring
the top-level one. Verified locally against both file shapes. Small out-of-slice fix,
but the whole Rundeck credential story depends on it.

**No Wire Stack job** — same reason as 600: slice 504's playbook does not exist.

### What live acceptance must confirm

`rd jobs load` for all 14 files, the jobs appearing under their three groups, the cron
on Check Native App Updates firing, and one run of each against a populated `config/`.

## 2026-07-26 — first live import and run

Target: LXC 13228 `pve-rundeck-4` on pve-host-3, Rundeck 6 at http://192.168.13.228:4440.
Runner checkout was 16 commits stale at `7df0301`; reset to `origin/master` `059316a`.

**Project `homelab-infra` created via the API**, not the UI —
`POST /api/47/projects` with `service.NodeExecutor.default.provider: local`. It did not
exist; the instance had zero projects.

**All 15 files imported clean** (14 plus `wire-media-stack.yaml` from slice 504), via
`POST /api/47/project/homelab-infra/jobs/import?dupeOption=update&uuidOption=preserve`
with `Content-Type: application/yaml`. Every file reported `succeeded=1, failed=0`.
Groups and the weekly cron on Check Native App Updates are as designed.

**`rd` is not required and was not installed.** The REST API accepts the same job YAML
the CLI sends, and `RUNDECK_API_TOKEN` from the repo's `.env` authenticates it. The
README's `rd jobs load` loop still stands as the documented path, but nothing depends
on the CLI being present.

### Defect found: every job failed identically on the first run

`ModuleNotFoundError: No module named 'yaml'` → `ERROR: failed to parse Proxmox
connection from ../config/proxmox.yml`, exit 1, before Ansible was ever reached.

No job step puts `$VENV` on `PATH` — it calls `"$VENV/ansible-playbook"` by absolute
path — so the `python3` hardcoded inside `with-proxmox-env.sh` resolved to the distro
interpreter, which has no PyYAML. **This blocked all 15 jobs, not just Lab Status.**

Fixed in the wrapper rather than in 15 job files (`059316a`): it now resolves `$PYTHON`,
then the `python3` sibling of the ansible command it is handed (necessarily the venv's
own, since Ansible itself needs yaml), then `PATH` — taking the first that actually
imports yaml, so a genuinely missing module fails there with an actionable message.
Job definitions are untouched. The alternative — `apt install python3-yaml` on the
runner — was rejected: it fixes one host and leaves the next clone broken.

### Result

Lab Status (execution 2) succeeded end to end: Rundeck script step → wrapper →
venv ansible → Proxmox API → rendered report. Independently confirmed the token
authenticates rather than returning an empty set — `ansible-inventory --list` sees
57 LXCs, 4 QEMU, 3 nodes.

Acceptance items 1, 2 and 4 are met. **Item 3 is one job of fifteen** — Lab Status is
the only one that has run, and it is the only read-only one. The fourteen that mutate
the lab remain unobserved.

## The four never-run jobs were run, 2026-08-12 — and three were broken

Executions 103–121. Every one of these was gate-green and had shipped unusable. None of the
defects is visible to lint or `--syntax-check`; all three needed one execution to find.

| Job | What happened | Fix |
|---|---|---|
| Tail App Log (103) | Every guest UNREACHABLE — connected as `rundeck@`, the account running Ansible | The Proxmox dynamic inventory carries no `ansible_user`. `check-native-updates.yml` and `status.yml` already load it from config and say why in a comment; `tail-applog.yml` and `restart-app.yml` were written without it |
| Migrate Servarr (107) | Died on line 4, `bad substitution` | The script step used `${option.instance}`. Rundeck does not expand those inside a script step — every other job in `rundeck/jobs/` reads `RD_OPTION_*` and carries a comment saying exactly this |
| Rollback Container (106) | Recreated the container, **skipped the health probe, exited 0** | The probe was gated on `_rb_instance_config.app.port`, but ports are declared in `vars/app-defaults/`. W4's fatal gate had never been reachable. Now resolves port from instance config → app-defaults → the Compose file's own published port |
| Check Native App Updates (104) | Green first time | — |

**A job that has never run is not "built", whatever the gates say.** Three of four is not a
sampling error; it is what shipping without executing looks like. The Config group is the
last never-run group and should be assumed broken until a run says otherwise.

`Reimport Jobs` was needed before the fixed definitions were live (execution 108) — Rundeck
holds its own copy, exactly as the standing fact in INDEX says.
