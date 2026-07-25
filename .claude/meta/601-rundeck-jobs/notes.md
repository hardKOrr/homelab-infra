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
