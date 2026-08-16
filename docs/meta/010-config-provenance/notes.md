# 010 — notes

Design narrative and build record, moved out of README.md during the meta restructure
(2026-08-08). The README now carries goal, remaining acceptance and links; everything
below is the reasoning and history behind it, kept verbatim.

## Problem

Every job in both UIs runs only because `config/proxmox.yml` happens to exist on one LXC.
That file is hand-written, gitignored, unversioned, unvalidated, absent from every backup,
and carries a root-privileged Proxmox API token in plaintext. Nothing in the repo creates
it, checks it, or can reconstruct it.

Two defects, not one.

### Defect A — nothing creates the config, and it cannot travel

The operator authors config on a workstation. The runner is an LXC on the cluster.
`config/` is gitignored (`.gitignore:6`), so **there is no mechanism that moves an edit
from the machine where it is written to the machine that executes it.** This is the
default homelab shape, not a corner case. Today the only transport is SSH and a text
editor, performed by hand, on a host whose contents exist nowhere else.

`bootstrap-rundeck.sh` compounds it: it stands up a complete runner — LXC, Java, Rundeck,
an ansible venv, the repo clone, an admin password, a non-expiring API token — and then
stops one step short of the thing that makes any of it usable. Its closing summary lists
four outstanding items (`bootstrap-rundeck.sh:363-368`); config is not even among them.

### Defect B — the file classes are miscounted, and shape is fused to secret

The original form of this slice listed four files and missed the class that grows:

| # | File(s) | Content | Written by | Recoverable? |
|---|---|---|---|---|
| 1 | `config/proxmox.yml` | shape + `api_token_secret` | a human, once | no |
| 2 | `config/infrastructure.yml` | shape + `vaultwarden.admin_token` | a human, once | no |
| 3 | **`config/apps/<instance>.yml`** | **one per app, unbounded** | **a human, per deploy** | **no** |
| 4 | `config/.generated/facts.yml` | ~10 service tokens + endpoints | bootstrap | only by re-running bootstrap |
| 5 | `.env` | Rundeck API token | a human, once | no |

**Class 3 is the omission that matters.** It is the lab definition — which apps exist, on
which stack, at which identity mode, in which estate. Losing it does not merely cost
credentials; it costs the description of the lab. It also grows with every deploy, so it is
the one class a one-time hand-placement model degrades against fastest.

Across classes 1–3 two different things are fused into one opaque file:

1. **Shape** — `api_host`, node, networks, `ssh_public_key`, provider choices, stack
   assignments. Not secret. Belongs in review, in history, in a backup.
2. **Secret** — `api_token_secret`, `vaultwarden.admin_token`. Genuinely must not be in
   git; CLAUDE.md's secrets model already sanctions the alternative ("gitignored `config/`
   files **or** Semaphore env vars").

Fusing them imposes the secret's constraint on the shape, and the shape inherits the
secret's invisibility. A wrong or missing key then surfaces deep inside a playbook —
slice 601's first live run died at `ModuleNotFoundError` inside a config parser, which is
the polite version of the same problem.

### Defect C — the runner is outside the model it runs

`bootstrap-rundeck.sh` creates LXC 13228 and never tags it `homelab-infra`. Consequences,
all live today:

- `configure-pbs.yml:210-217` builds the vzdump vmid list by filtering guests on that tag,
  so **the host holding the platform's only copy of its own config is the one guest the
  backup job excludes.**
- `status.yml` and `check-native-updates.yml` walk the same tag, so the runner is invisible
  to the lab's own reporting.
- The runner is not described by any `config/apps/*.yml`, so nothing records its VMID,
  address, checkout path or venv path except a comment in INDEX.md.

The platform manages what it creates. It created the runner. It does not manage it.

## Approach

Five moves. Config lives on the runner, and the runner becomes a managed guest.

### 1. Bootstrap authors the config — it does not ask a human to

`bootstrap-rundeck.sh` runs **as root on a Proxmox node**. Most of `config/proxmox.yml` is
therefore discoverable, not promptable:

| Key | Source |
|---|---|
| `api_host`, node name | `hostname`, the node's own address |
| storage | `pvesm status` |
| bridges | `ip -o link` |
| template storage | `pvesm status --content vztmpl` |
| `ssh_public_key` | already collected (`bootstrap-rundeck.sh:50`) |

What genuinely needs asking: domain, guest IP band + gateway, timezone, and the four
provider choices. Six prompts, five with defensible defaults. Each also reads an env var,
so the scripted path stays non-interactive and the interactive path stays humane.

The script then **writes `config/proxmox.yml` and `config/infrastructure.yml`** into the
runner's checkout. This is the direct answer to "nothing creates it."

### 2. Bootstrap mints the Proxmox token — the plaintext root token disappears

Node root can issue its own credential:

```
pveum user token add homelab-infra@pve automation --privsep 0 --output-format json
```

The secret is displayed once, at creation. Capture it in-process and write it straight to
Rundeck Key Storage (`keys/proxmox/api-token`) / the Semaphore environment. **It never
touches a file.** `config/proxmox.yml` ships with `api_token_secret` absent, and the
wrapper and loader read `PROXMOX_API_TOKEN` from the environment instead.

This retires `root@pam!rundeck` in favour of a dedicated `homelab-infra@pve` user with a
scoped ACL role — a real privilege reduction, and this is the only moment it is cheap.

A token secret cannot be re-read, so re-runs keep the existing token by default and rotate
only on an explicit flag.

### 3. The runner's `config/` is the source of truth, and the UI reaches it both ways

No second repo, no nested checkout, no forge account. `config/` on the runner is the one
authoritative copy, and both UIs gain the jobs that make it reachable:

| Job | Playbook | Direction |
|---|---|---|
| **Configure App** | `maintenance/configure-app.yml` | writes `config/apps/<instance>.yml` from survey parameters |
| **Get Config** | `maintenance/get-config.yml` | reads `config/` back out, secrets redacted |
| **Config Doctor** | `maintenance/config-doctor.yml` | validates the authored set, mutates nothing |

One parameterised `Configure App`, not one per app — an authoring interface without
building one, at the cost of one job-list entry rather than N. `Get Config` takes an
optional `instance` (empty = the whole set) and emits YAML to the job log and to
`artifacts/config-<timestamp>.tar.gz`. Together they are the transport in both directions:
the workstation no longer needs SSH to read or write the lab definition.

### 4. Durability and history come from parts the project already owns

Losing the runner must not lose the lab definition, and an edit must be inspectable before
and after it lands. Three mechanisms, all existing:

| Property | Mechanism |
|---|---|
| Off-host durability | **PBS** — move 5 makes the runner a backed-up guest |
| Per-edit history | every config write goes through `tasks/config/write-config-file.yml`, which keeps a timestamped copy under `config/.backups/<file>.<ts>` before replacing it |
| Visible change | the same task runs with `diff: true` and the unified diff lands in the job log, so a `Configure App` run shows exactly what it changed |
| Off-host copy on demand | `Get Config` archive — keep it wherever the operator likes |

`config/.backups/` is inside the gitignored `config/` tree, so 012's `reset --hard` refresh
cannot touch it, and PBS carries it off the host with everything else.

### 5. The runner joins the model it runs

`bootstrap-rundeck.sh` adopts its own LXC:

- **tags it `homelab-infra`** — this alone puts it in the PBS backup job, `status.yml` and
  `check-native-updates.yml`
- **writes `config/apps/rundeck.yml`** describing itself: vmid, hostname, address, cores,
  memory, `app.checkout_path`, `app.venv_path`, `app.service_name: rundeckd`
- **records a `runner` registry key** in `config/.generated/facts.yml` (host, vmid, checkout,
  venv) so playbooks and `status.yml` can name the host they are running on

Out of scope: a `roles/rundeck/` that provisions the runner from Ansible. The script owns
creation — it must, since it runs before any of this exists. Adoption is the depth that
matters, and it is three edits.

### 6. Fail at the front door

**`config-doctor`** validates the whole authored set against `vars/CONTRACT.md` in one
pass, naming every missing key by file and path, before anything mutates. It runs as a
pre-flight step in every job — including `Bootstrap Platform`, where a missing key today
surfaces as a stack trace mid-provision — and as a job of its own.

## Files

- `rundeck/bootstrap-rundeck.sh` — discover + prompt; write `config/proxmox.yml` and
  `config/infrastructure.yml`; mint the Proxmox token via `pveum`; create
  `homelab-infra@pve` with a scoped role; stage Key Storage; **tag the runner LXC
  `homelab-infra`**; write `config/apps/rundeck.yml` and the `runner` registry key.
  Rewrite the closing summary (it still lists slice 601's completed work as outstanding).
- `semaphore/README.md`, `semaphore/project.json` — the same staging for the Semaphore path:
  `PROXMOX_API_TOKEN` in the `Homelab` environment, not in a file.
- `ansible/scripts/with-proxmox-env.sh:54` — fall back to `PROXMOX_API_TOKEN` /
  `PROXMOX_API_TOKEN_ID` from the environment when the config file omits the secret; today
  it hard-fails on the missing key.
- `ansible/tasks/load-user-vars.yml` — the same fallback on the playbook side.
- `ansible/tasks/config/write-config-file.yml` (new) — the one path by which any playbook
  writes a `config/` file: back up to `config/.backups/<file>.<ts>`, write, emit the diff.
- `ansible/scripts/config-doctor.sh` (new) — validate `config/` against `vars/CONTRACT.md`;
  report every missing/malformed key at once with file and key path; exit non-zero.
- `ansible/playbooks/maintenance/config-doctor.yml` (new) — the same check as a job.
- `ansible/playbooks/maintenance/configure-app.yml` (new) — write `config/apps/<instance>.yml`
  from survey parameters via `write-config-file.yml`.
- `ansible/playbooks/maintenance/get-config.yml` (new) — dump `config/` (redacted) to the
  job log and to an archive; optional `instance` parameter.
- `ansible/playbooks/bootstrap.yml` — run `config-doctor` as the first play, before
  Vaultwarden provisions anything.
- `rundeck/jobs/*.yaml`, `semaphore/project.json` — inject the secret into the step
  environment; add Config Doctor, Configure App and Get Config to the UI.
- `config.example/proxmox.yml` — document the empty-secret + env-var form as the recommended
  shape.
- `config.example/apps/rundeck.example.yml` (new) — the runner's own instance file.
- `ansible/vars/CONTRACT.md` — §3 gains the `runner` registry key; document
  `config/.backups/` and the redaction key list `Get Config` honours.
- `rundeck/README.md`, `semaphore/README.md` — one section: these paths, this Key Storage
  entry, restore in this order.


## Decided

- **The runner's `config/` is the source of truth. Operator decision, 2026-07-27
  (revising the earlier decision below).** Transport was the binding constraint; `Configure
  App` and `Get Config` remove it using jobs the UI already has a home for. A private lab
  repo cloned into `config/` is not built: it needed a forge account, a deploy keypair, a
  manual repo-creation step this slice could not automate, and a second git checkout nested
  inside the path the outer repo ignores. Durability and history come from PBS, from
  `config/.backups/`, and from the diff in the job log — parts this project already owns.
  *Superseded:* the 2026-07-27 morning decision that "the authored shape lives in a private
  lab repo."
- **Vaultwarden is the store for *generated* secrets, not for authored config.** Classes 1
  and 2 must be readable before Vaultwarden exists — it is deployed *by* this platform as
  bootstrap step 1 — so they can never live there. Class 4 can and should; see 014.
- **Accepted cost:** history is point-in-time, not per-commit-with-message. `config/.backups/`
  plus a nightly PBS snapshot answers "what did this look like before" and "get it back."
  It does not answer "who changed this and why." For a homelab lab definition that is the
  right trade; a lab that wants review can keep `Get Config` output in a repo of its own,
  and nothing in the platform depends on whether it does.

## Open questions — all resolved by the operator, 2026-07-26

- **~~Does `config/.generated/facts.yml` stay a real file?~~** **Yes, it stays real** —
  vault is truth, file is a cache. Keeps offline runs working and is simpler. Carried
  into 014.
- **~~How far does the scoped `homelab-infra@pve` role go?~~** **Derived and implemented.**
  The privilege list and the reasoning per group are in "The Proxmox role" in
  `rundeck/README.md`. Honest caveat recorded there: `Sys.Modify` at `/` is required —
  registering PBS as a storage backend and creating the cluster vzdump job are both
  cluster-configuration writes — so the API-token reduction from `root@pam` is real (no
  user, realm, permission or ACL management) but not dramatic. The separate automation
  SSH identity still has the node-root channel required by delegated `pct`/`qm` tasks.
- **~~Does `config/.backups/` need pruning?~~** **Yes, keep the last 20 per file.** Four
  lines in `write-config-file.yml`.
- **~~How does `Configure App`'s survey stay in step with the instance schema?~~** **The
  common dozen keys plus an `extra_yaml` free-text field**, and the job description says so.
  No per-app jobs: which app an instance file belongs to is decided by which Deploy job runs
  against it, so `Configure App` does not need to know, and the job list gains one entry
  rather than N.

## Built — 2026-07-26

Landed together with 012, as INDEX.md recommended: they share `bootstrap-rundeck.sh`, both
UI job sets and both READMEs. Both gates green (148 lint files, 22 playbooks syntax-clean).

### Verified locally

The four ticked acceptance items above were exercised on the workstation against a scratch
`config/` tree — `ansible-playbook` run directly, not through Rundeck. That covers the
config-authoring half of the slice end to end: create from nothing, merge a single field
while the others survive, unified diff in the log, timestamped backup, a true no-op on
re-run, malformed input refused before anything is written, and `Get Config` returning what
`Configure App` wrote. **It does not cover anything the bootstrap script does** — no
`pveum`, no Key Storage, no tagging, no job import. Those need the live run.

Four defects were found and fixed by that exercise, all of which would have shipped:

- `write-config-file.yml` used `copy: remote_src=true` for the backup, which routes through
  `atomic_move` and fails outright where its chmod step is unsupported. Replaced with
  slurp-then-write.
- A generated timestamp in the instance-file header made every write differ from the last,
  so nothing was ever idempotent and `.backups/` filled with identical copies. Removed —
  the backup filenames and the file mtime already record when.
- `get-config.yml` resolved `scripts/` one directory too high, and reported the resulting
  "file not found" as "no config file for that instance", which sends the reader looking in
  entirely the wrong place. Path fixed; the failure message now carries the script's stderr.
- `configure-app.yml` would happily write `app: null` from an `extra_yaml` block that lost
  its indentation — which at deploy time clobbers the whole `app:` subtree of the defaults,
  because the per-app merge is recursive. Now refused with an explanation.

Both playbooks also now hand `ansible_playbook_python` to the shell scripts they call, so
the "no python3 with PyYAML" class of failure — the one that broke all 15 jobs on the first
live run (`059316a`) — cannot recur through this path.

### Deviations from the plan above, and why

- **The Config jobs are their own `Config` group, not part of `Bootstrap`.** Operator
  decision. They are day-2 operations that outlive bootstrap by a long way, and burying
  three routinely-used jobs under a group named for a once-ever action reads wrong.
- **`Get Config` is not a Rundeck "artifact".** Rundeck OSS has no artifact-download
  feature — that is Enterprise. The redacted YAML goes to the job log, which is the copy an
  operator actually reads and pastes back into `Configure App`; the unredacted `tar.gz` is
  written under `artifacts/` on the runner and its path is printed. Getting *that* file off
  the host is scp, or PBS. The acceptance criterion is met in substance — the config is
  readable from the UI alone — but not in its literal wording.
- **The Proxmox secret reaches a job through `/etc/homelab-infra/secrets.env`, not from Key
  Storage directly.** Key Storage holds it at `keys/proxmox/api-token` as the durable copy,
  exactly as planned. But Rundeck OSS cannot inject a Key Storage value into a plain script
  step's environment without adding a secure option to every job file — which is precisely
  the per-job duplication 012 exists to remove. A root-owned 0640 root:rundeck file, outside
  the checkout and outside `config/`, gives the same isolation with none of the duplication.
  **Flag for live verification:** if a secure option with `storagePath` does resolve without
  prompting on this Rundeck build, prefer it and delete the file.
- **A fourth Config job, `Reimport Jobs`, was added.** Not in the plan. It closes the half of
  "the runner runs current master" that the checkout refresh cannot reach — job *definitions*
  live in Rundeck's database, not in the working tree. See 012.
- **`redact-config.sh` walks the parsed structure** rather than the planned explicit key
  list. A substring match on `token|secret|password|api_key|arl` covers every provider's
  spelling without enumerating them and cannot be defeated by quoting or folding. Cost:
  comments do not survive the round trip, so the log is a view and the archive is the
  restore point.
- **`ansible.timezone` was added to `config/proxmox.yml`.** The bootstrap script asks for a
  timezone and had nowhere contract-sanctioned to put it. Additive; nothing reads it yet.
