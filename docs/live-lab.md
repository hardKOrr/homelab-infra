# Live-lab evidence for agents

This is the operator how-to for collecting live-lab evidence from an agent session. The
acceptance claims remain in [`CONTRIBUTING.md`](../CONTRIBUTING.md). The three
allowed PR **Live-lab status** values are enumerated in the
[`PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md). The normative boundary is in
[`specs/one-click-idempotent.md`](specs/one-click-idempotent.md): every live-lab action
uses a repository-owned Ansible playbook or Rundeck job. Do not use the API workflow here
to bypass that boundary.

This guide is the authoritative `rd` reference for this repository. Where a
workstation-local copy of these commands disagrees with this file, this file wins.

## Routing an agent here

A worker only uses this workflow if something points at it. That pointer lives in the
operator's agent configuration, outside this repository, because the agent runner is not
a homelab-infra concern. This repository owns what the pointer must say:

> For Rundeck lab access, source the operator's credential file, then use
> `docs/live-lab.md` in the homelab-infra checkout for `rd` usage and the evidence
> workflow.

A pointer that sends an agent to a workstation-local command list instead is a defect.
Such a list drifts out of review: it cannot be gate-checked, it does not travel to another
workstation, and it will keep describing commands this guide has already corrected or
prohibited. Point at this file and delete the duplicate.

## Agent operating context

AO project configuration owns the private access pointers and standing rules for both
workers and orchestrators. Forward `HOMELAB_LAB_DIR` (the private operator directory) and
`HOMELAB_LAB_VALUES` (its `lab-values.yml`) into both roles. Point their rules at this
guide, `AGENTS.md`, and the current GitHub issue body. Store paths in AO configuration,
not token values or copies of lab configuration. Updating project rules affects newly
created sessions; provide the same instruction to an existing session before lab work.

The private inputs and their owners are:

| Input | Owner and location |
| --- | --- |
| Current node addresses/names, runner identity, domain, network and storage placeholders | Operator's `lab-values.yml`, under `current:`; `retired:` is leak-check input only |
| Rundeck API access | Operator's `rundeck-access.env`, with `RD_URL` and `RD_TOKEN`, mode `0600` |
| Proxmox root access for bootstrap and offline recovery | Operator's SSH identity and SSH configuration; use its configured relay when present |
| Desired networks, placement, storage and application instances | Runner's user-owned `config/` tree; inspect via Get Config and validate with Config Doctor |
| Ordinary automation credentials | Canonical Vaultwarden items and encrypted Rundeck Key Storage, established by bootstrap/cutover |
| Recovery while the runner or vault is unavailable | Independent operator recovery record, as specified by [runner recovery](../rundeck/RUNNER-RECOVERY.md) and [vault recovery](../rundeck/VAULTWARDEN-RECOVERY.md) |

Never substitute a retired endpoint for a missing current value. A root SSH connection
to a Proxmox node proves node access, not Rundeck API access, runner-to-node access, or
service routing. Verify those separately. Read-only Proxmox API/SSH inspection is
appropriate for inventory, tags, storage, worker tasks and recovery-point checks.
Mutations use repository-owned playbooks, jobs or the documented node-side bootstrap and
offline recovery entry points. Do not create an ad-hoc API/SSH implementation of an action.

### Unattended bootstrap inputs

Read the current `bootstrap-rundeck.sh` tunables and [bootstrap guide](../README.md) before
running on a named node. Supply `NONINTERACTIVE=1` and the unresolved inputs through a
root-private environment file: `LAB_DOMAIN`, `VAULTWARDEN_OWNER_EMAIL`, the dedicated
automation account if overriding its default, and `CLOUDFLARE_API_TOKEN` when using the
Cloudflare DNS-01 provider. The token needs Zone Read and DNS Edit for the lab zone.
The DNS-record wiring provider and the ACME DNS-01 provider are separate concerns; one
provider's credential does not satisfy the other.

Declare or verify the runner's free address/prefix, gateway, bridge/VLAN, DNS server and
storage (`CT_IP`, `CT_GW`, `CT_BRIDGE`, `CT_VLAN`, `CT_DNS`, `CT_STORAGE`), the guest network,
address allocation bands, internal client CIDRs, timezone, domain estates and node
placement. Existing runtime configuration is the authority on a rerun; do not assume
that supplying a new environment value rewrites an already answered configuration key.
Use the supported configuration path and verify the result. The Vaultwarden HTTPS name
must resolve to Caddy from the runner and operator, with 80/443 reachable and declared CA
trust usable. Verify Enrollment, Cutover, both durable markers and Bootstrap Platform
separately. Do not fabricate markers or bypass ordinary jobs' Vault-mode guard.

Do not put a bootstrap credential in a shell command, GitHub body, job option or captured
log. Load it from its private file without tracing, and retain recovery material outside
the components it unlocks. Name unresolved inputs in the issue body instead of prompting
halfway through an unattended run.

### Serial live operations and disposable fixtures

One worker owns live operations across this lab at a time, including read-only Rundeck
jobs. Every execution refreshes one shared runner checkout; concurrent jobs can replace
the files another execution is using. Coordinate across AO project registrations, not
only within one orchestrator. Hold a common workstation lock for the complete observation:

```bash
exec 9>"$HOMELAB_LAB_DIR/operation.lock"
flock -n 9 || exit 75
# Keep this shell and descriptor alive through all jobs and final verification.
```

Check running executions and the current issue body before starting. If a previous
execution or node-side operation continues, keep the lab occupied until its final state
is known. A lock on one workstation does not exclude a human or another workstation;
coordinate those actors as well. Repository implementation and offline checks can
proceed independently. Confirm the runner's `LAB_BRANCH` and execution revision before
each observation; import changed job definitions through Reimport Jobs.

The issue states whether the target is a disposable pre-production environment and
which operations are authorized. Record the exact instance, namespace/guest, shared
workloads, selected storage paths, recovery method and cleanup boundary before mutation.
Public text uses placeholders; resolve them privately against current inventory.
Repeated install, restore and removal cycles are appropriate for owned disposable
targets with the required recovery path. Pre-production does not authorize formatting
an external export, changing a management firewall or destroying an untagged resource.

Group test services by their declared dependencies and shared recovery units. Keep
application Deploy jobs individually callable. Start with small applications requiring
no external account or hardware; add a named database or media fixture when required.
Use generated, non-sensitive files with checksums to verify reads, writes, permissions,
mounts and links. External NFS/storage needs its owner's exact export/path, capacity,
failure-domain and recovery declaration before use. Retain fixtures until every dependent
issue has finished. Keep GPUs, USB devices, provider accounts and appliance installation
as explicit prerequisites only for the products that require them.

## Recovery observation

Read the product defaults, catalog recovery declaration, rendered maintenance actions
and [recovery acceptance](recovery-acceptance.md) before choosing a method. A PBS guest
restore affects the whole VM/LXC, including all applications sharing it. External bind
mounts, provider state and devices require their own recovery evidence. Application-native
archives must cover the declared database and durable files together. Kubernetes
application recovery acts on the namespace/PVC or native dump, not a cluster-node restore.
Rebuild-only products prove reconstruction and service behavior without a durable-data
claim. Runner, Vaultwarden and PBS recovery must have independent unlock/datastore access;
the target cannot supervise or unlock its own recovery.

Install, functional checks and a convergent second deployment may run before a new drill
dispatcher exists. Mark restore acceptance pending until the applicable supported method
has actually been exercised. Never dispatch a product through an unsupported method to
clear a checkbox. Missing automation or a wrong Rundeck description is separate repository
work, linked from the observation body.

For a stateful proof, create distinguishable fixture state A, capture and verify its
artifact, then change the disposable target to B. Plan the restore first. Before existing
replacement, verify the independent B recovery point; after restoring A, assert A survives,
B-only state is absent, the service and its dependents work, and guest identity/`onboot`
are preserved when guest recovery is used. Exercise a stopped/isolated new destination
when the method supports it, with no duplicate address or writer. Record failure/retry
behavior and retain recovery points through acceptance. A green job or a listed snapshot
alone is not data-recovery evidence.

Operator instructions belong in the applicable Rundeck Backup/Restore job descriptions:
recovery unit, source artifact, target selection, plan/execute options, independent point,
expected end state and retry path. Verify the imported UI instructions against the source
as part of the observation. Keep current results, prerequisites and redacted execution
evidence in the issue body; replace stale instructions rather than appending corrections.

## Before touching the lab

1. Read the issue's acceptance criteria and identify the exact target, instance,
   and maintenance or recovery behavior that applies. Do not broaden a live observation
   into an unrelated fix.
2. Confirm the CLI is present and record its version:

   ```bash
   rd --version
   ```

   The gotchas in this guide were verified against `rd` 2.2.1. Re-check them if the
   workstation reports a different version. `rd` is an operator-installed tool, not a
   repository dependency; this repository never vendors, installs, or pins it.

3. Load the user-owned credentials with export enabled:

   ```bash
   set -a; source ~/.config/ai/homelab-infra/rundeck-access.env; set +a
   ```

   Plain `source` leaves the variables unexported and `rd` fails with `RD_URL is
   required`. The file under `~/.config/ai/homelab-infra/` is outside this repository. Never print
   or commit its token, its `RD_URL` value, or any other credential; do not copy it into
   `config/` or a captured evidence file. The real lab values behind every placeholder in
   [`lab-placeholders.md`](lab-placeholders.md) sit beside it in `lab-values.yml`.

4. Confirm project access and discover the job by its name. Job discovery is read-only:

   ```bash
   rd projects info -p homelab-infra
   RD_FORMAT=json rd jobs list -p homelab-infra -j 'Lab Status' --verbose
   RD_FORMAT=json rd jobs list -p homelab-infra -j 'Config Doctor' --verbose
   ```

   Prefer the returned job ID for the run. `rd jobs info -i <JOB_ID> -v` reports only a
   job's identity and description — id, name, group, project, links, schedule state. It
   does **not** return the job's options, so it cannot tell you what to pass to a run. It
   also does **not** accept `-p`; the project is implied by the job ID.

5. Read the job's options from its definition, not from `jobs info`. Either source is
   read-only:

   ```bash
   # Repository source — authoritative, and the one to change:
   grep -A20 '^  options:' rundeck/jobs/<job>.yaml

   # What the project currently has imported, exported through the API:
   rd jobs list -p homelab-infra -i <JOB_ID> -F yaml -f /tmp/<job>.yaml
   ```

   Compare the two when a run rejects an option: a difference means the project is behind
   the repository and needs **Reimport Jobs**. An exported definition carries the Key
   Storage paths backing a job's secure options (`bw_clientid`, `bw_clientsecret`, and
   similar). Those paths are not secret values, but do not paste a whole export into a PR;
   quote only the option names and descriptions the evidence needs.

## Choose the owning job

Use the smallest job that exercises the behavior under observation. The source job set
and folder layout are maintained by [`rundeck/README.md`](../rundeck/README.md); use its
current names rather than inventing a command or a manual step.

| Change under observation | Job class to use |
| --- | --- |
| One application's deploy, configuration, wiring, restart, or other day-2 behavior | That application's **Deploy** job or its **Maintenance** action job, with the exact `instance` and other documented options |
| A lab-wide platform, integration, health, configuration, or storage behavior | The owning **Manage** job, such as **Lab Status**, **Config Doctor**, or the specific integration/storage job |
| A diagnostic or troubleshooting behavior | The owning read-only **Operate** job |
| A credential or restore behavior | The applicable **Setup** or **Recover** job, after checking its maintenance and recovery contract |
| A change to `rundeck/jobs/*.yaml` | **Setup → Automation → Reimport Jobs**, then the target job |

Playbook-only changes do not need **Reimport Jobs**: the next execution refreshes the
runner checkout. Job definitions are different. Once the branch containing the job
change is available to the runner's configured `LAB_BRANCH`, run **Reimport Jobs** through
Rundeck so the repository-owned definitions reach the project:

```bash
rd run -j 'Setup/Automation/Reimport Jobs' -p homelab-infra -f
```

It updates jobs by UUID and preserves their execution history. Do not import a raw job
file from a workstation or edit the job around the repository source.

## Run and capture an observation

Run the selected job through its Rundeck ID or group/name. `-f` follows the output until
completion, which is useful for the first run:

```bash
rd run -i <JOB_ID> -p homelab-infra -f
# Or, when the group/name is unambiguous:
rd run -j '<JOB_GROUP>/<JOB_NAME>' -p homelab-infra -f
```

Pass documented job options after `--` in Rundeck's `-<OPTION> <VALUE>` form, for example
`rd run -i <JOB_ID> -p homelab-infra -- -instance <INSTANCE>`. Option names differ per job
and many jobs take none at all — take them from the job definition as in step 5 above,
never by guessing from another job. Keep the same option values for both runs when proving
idempotence.

Prefer `-i <JOB_ID>` over `-j '<GROUP>/<NAME>'`. This project's groups contain spaces and
`&` (for example `Applications/Media & Entertainment/Media Servers/Jellyfin`), so the
group/name form has to be quoted exactly and is easy to get silently wrong.

For a run whose ID must be recorded separately, ask `rd` to return only the execution ID,
then inspect it through the execution API:

```bash
EXECUTION_ID="$(rd run -i <JOB_ID> -p homelab-infra --outformat '%id')"
rd executions info -e "$EXECUTION_ID" -v
rd executions state -e "$EXECUTION_ID"
rd executions follow -e "$EXECUTION_ID" -f
```

After completion, replay the full output from the beginning for the PR evidence:

```bash
rd executions follow -e "$EXECUTION_ID" -t
```

There is no `executions output` subcommand. Preserve the job name, execution ID, date,
target/options, result, and the relevant output excerpt. Replace every address, domain,
node name, and VMID in that excerpt with its placeholder from
[`lab-placeholders.md`](lab-placeholders.md). Include warnings and ignored
failures rather than presenting only a green summary. Put the resulting observation on
the observation issue, or in the PR's **Live-lab status** section when the PR itself is
being verified, using the status contract in [`CONTRIBUTING.md`](../CONTRIBUTING.md).

### Idempotence

For an authorized non-destructive acceptance run, execute the same owning job twice with
the same target and options. Capture a separate execution ID and output for each run and
compare the resulting state and relevant task changes. A successful second run that
reports no further changes is the useful evidence; running a read-only status job twice
only demonstrates repeatable observation, not playbook idempotence.

```bash
FIRST="$(rd run -i <JOB_ID> -p homelab-infra --outformat '%id')"
rd executions follow -e "$FIRST" -f

SECOND="$(rd run -i <JOB_ID> -p homelab-infra --outformat '%id')"
rd executions follow -e "$SECOND" -f
rd executions follow -e "$SECOND" -t
```

Do not run a converging, destructive, or disruptive job merely to create evidence. The
acceptance request must authorize the exact target and recovery path first.

## CLI checks and gotchas

The following commands inspect Rundeck state without changing the lab:

```bash
RD_FORMAT=json rd jobs list -p homelab-infra --verbose
RD_FORMAT=json rd executions query -p homelab-infra -d 1h
rd executions info -e <EXECUTION_ID> -v
rd executions state -e <EXECUTION_ID>
rd executions follow -e <EXECUTION_ID> -t
rd nodes list -p homelab-infra
rd projects info -p homelab-infra
```

Keep these distinctions in every session:

- `executions list` shows only currently running executions. Use `executions query` for
  completed execution history. For example, filter history by job with
  `RD_FORMAT=json rd executions query -p homelab-infra -i <JOB_ID>`.
- Do **not** pass `--verbose` to `executions list` or `executions query`; on `rd` 2.2.1 it
  throws a `NullPointerException` when an execution has a null description, which most
  have. `--verbose` is safe on `jobs list`.
- `jobs info` returns identity and description only, never options. Use
  `rd jobs list -i <JOB_ID> -F yaml -f <path>` or `rundeck/jobs/*.yaml` for a full
  definition.
- `jobs info` and `executions info`, `executions state`, and `executions follow` do not
  accept `-p`. The job ID or execution ID supplies the project for those commands.
- Read-only API inspection is allowed, but `rd run -F '<filter>' -- <command>` and
  `rd run --script <path>` are never an agent path to the lab. Use the owning playbook or
  job instead.

## Worked example

The following excerpts were captured from the `homelab-infra` project on 2026-09-16 by
running **Lab Status** (execution `10`) and **Config Doctor** (execution `11`) through
Rundeck. They show the shape of evidence to paste into a PR; they are not a promise that
later runs will have the same guests, updates, or timestamps. Volatile runner URLs and
address/VMID columns are elided so no configured `RD_URL` value is recorded.

```text
# Execution started: [10] ... Manage/Lab/Health/Lab Status
HOMELAB RUN --------------------------------------------------------
  Playbook   status.yml
  Revision   92caa89 docs: prevent AO worker respawn loop from issue-linking guidance (#197)
  Branch     master

LAB STATUS — 2026-09-16 21:32:56
GUESTS (10 tagged _+lab)
  caddy                  running  ...  stale services 0
  homelab-rundeck        running  ...  stale services 0
  k3s-1                  running  ...  stale services 0
  k3s-2                  running  ...  stale services 0
  k3s-3                  running  ...  stale services 0
  ...
KUBERNETES
  k3s-1                  Ready
  k3s-2                  Ready
  k3s-3                  Ready
NATIVE APP UPDATES
  vaultwarden            1.37.2 -> 1.37.3   re-run its deploy job to update
  ntfy                   2.27.0 -> 2.28.0   re-run its deploy job to update
HOMELAB RESULT -----------------------------------------------------
  Result     SUCCEEDED
  Duration   22s
  Changed    0 task(s) on 11 host(s)

# Execution started: [11] ... Manage/Configuration/Validation/Config Doctor
config-doctor: /var/lib/rundeck/homelab-infra/ansible/playbooks/maintenance/../../../config
  proxmox.yml         present
  infrastructure.yml  present
  apps/               1 instance file(s)
  proxmox token       from PROXMOX_API_TOKEN (environment)

OK -- no problems found.
HOMELAB RESULT -----------------------------------------------------
  Result     SUCCEEDED
  Changed    0 task(s) on 1 host(s)
```

Record ignored failures and warnings from a run like this even when the job result is
`SUCCEEDED`; they bound what health the evidence can claim.
