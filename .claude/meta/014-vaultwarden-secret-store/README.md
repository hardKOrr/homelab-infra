# 014 — Make Vaultwarden the mandatory secret store

**Status:** open
**Depends on:** 013 (Vaultwarden admin token self-capture), 200 (write-generated-facts)
**Blocks:** the durability half of 010; every deploy after the bootstrap cutover

## Implementation status (2026-08-02)

Construction is present and locally gated; the slice remains **open** until a live
enrollment/cutover and fail-closed observation are approved and completed.

- Local: all 25 playbooks pass syntax check, lint is clean, and focused tests cover item
  mapping, secret-field rejection/sanitization, Seed/Vault guards, preflight failure,
  private CLI cleanup, and child exit propagation.
- Live compatibility: the operator confirmed Rundeck package `6.0.1.20260715-1`, which
  satisfies the guarded 6.0+ AES-GCM requirement.
- Live migration: not run. No live secret was read, rotated, deleted, or printed. The
  exact field-name-only manifest must be inventoried and approved before cutover.

## Goal

Bring Vaultwarden up as early as possible, move the secrets needed to bootstrap it out of
their temporary files and into the vault, delete those file copies, and make Vaultwarden
the only source and sink for sensitive platform data from that point forward.

There are exactly two operating modes:

1. **Seed mode:** a fresh or explicitly recovered runner may read the minimum secrets from
   bootstrap files long enough to deploy Vaultwarden and prove that it is usable.
2. **Vault mode:** after the cutover marker is written, every deploy reads its required
   secrets from Vaultwarden and writes every new or changed secret back to Vaultwarden.

Vault mode is fail-closed: **no reachable and unlockable Vaultwarden means no deploy.**
Offline deploys and a secret-bearing `facts.yml` cache are deliberately not supported.
Only the explicit bootstrap/recovery path may cross that boundary.

## Problem

The current implementation contradicts that model:

- `/etc/homelab-infra/secrets.env` supplies the Proxmox token to every job indefinitely.
- `/etc/homelab-infra/secrets.d/vaultwarden.env` permanently holds the generated
  Vaultwarden admin token.
- Generated service credentials live in `config/.generated/facts.yml`; Vaultwarden is not
  involved in their reads or writes.
- The two files under `ansible/tasks/bitwarden/todo/` are documentation links, not an
  implementation.
- Jobs tolerate an absent facts file and can continue without proving that the secret store
  is available.

This creates several sources of truth and makes a Vaultwarden outage invisible until some
later operation happens to need a missing credential. It also leaves irreplaceable values,
such as the PBS API token secret, on one runner filesystem.

## Security boundary

A secret store cannot provide the credential used to unlock itself. Vault mode therefore
has one narrow external dependency: a machine credential/session bootstrap that can open
Vaultwarden. It must come from Rundeck Key Storage or the Semaphore environment, never
from the seed files or the repository, and it may exist in process memory only for the
duration of a job.

That credential is not a second application-secret store. It may unlock the vault; it may
not carry Proxmox, service, application, or user credentials. The existing Vaultwarden
admin token is not assumed to be a Bitwarden item-read credential; the implementation must
use a supported machine/client authentication flow.

## Approach

### 1. Separate bootstrap from normal operation

Give the runner an explicit, durable cutover state rather than inferring it from whichever
files happen to exist.

- Before cutover, only the Vaultwarden bootstrap/recovery workflow may load the seed files.
- Every ordinary job refuses to run before cutover.
- After cutover, `lab-run` refuses to source the seed files even if somebody recreates
  them accidentally.
- Re-entering seed mode is an explicit recovery action, not an automatic fallback.

The fresh-lab order is:

1. create the runner and its temporary seed files;
2. deploy Caddy so the enrollment endpoint is HTTPS;
3. deploy Vaultwarden and invite the exact owner and automation addresses;
4. complete the human owner/automation-account ceremony and verify machine access;
5. import the Proxmox token, Vaultwarden admin token, and any other secret already present
   at bootstrap;
6. read every imported value back and compare it without logging it;
7. write the cutover marker;
8. remove the seed files;
9. continue the platform bootstrap in Vault mode.

The transition must be resumable. A failure before verified cutover leaves seed mode intact;
a failure after the marker never silently falls back to files.

### 2. Make Vaultwarden the runtime read path

At the front door of every deploy, unlock Vaultwarden and resolve all secrets required by
that play into an in-memory runtime structure. Secret values must not be written to
`config/`, Ansible fact-cache files, job option values, artifacts, or managed-guest metadata.

`config/.generated/facts.yml` may remain only for non-sensitive discovery data such as
hosts, ports, VMIDs, instance names, and provider selection. Every token, password, private
key, API key, API secret, and credential field moves out of it. Existing consumers must be
changed to receive those fields from the runtime vault data instead.

Vault access is a mandatory preflight, not a lazy lookup halfway through a role. Failure to
reach, authenticate to, unlock, or read the required items stops the job before any
infrastructure mutation.

### 3. Make Vaultwarden the generated-secret write path

Each role that creates or rotates a secret writes it directly to the canonical Vaultwarden
item and verifies readback with `no_log: true`.

- Generate-before-apply flows store the value before applying it to the service.
- Create-once APIs, such as PBS token creation, store the returned value immediately in the
  same guarded block.
- A job may not report success until the vault contains the value it installed.
- A failed vault write is a failed deploy; the error reports the item identity and phase,
  never the value.

There is one item per registry role key
(`homelab-infra/sso`, `/notifications`, `/monitoring`, `/metrics`, `/backups`, `/dns`,
and `/vaultwarden`) and one per application instance for `media` entries. Field names
mirror the runtime contract so reads and writes do not require a second mapping.

### 4. Remove the old secret stores

After verified cutover:

- delete `/etc/homelab-infra/secrets.env`;
- delete `/etc/homelab-infra/secrets.d/vaultwarden.env` and the directory when empty;
- remove secret fields from `config/.generated/facts.yml`;
- remove file/env fallback for Proxmox and generated service secrets from normal jobs;
- reject newly written secret-shaped keys in `facts.yml`;
- update Get Config, backup, and diagnostic paths so none treats the deleted files as a
  recovery source.

A separately invoked recovery procedure may recreate seed material long enough to restore
Vaultwarden. It is not part of an ordinary deploy and must never activate automatically
when the vault is down.

## Files

- `ansible/tasks/bitwarden/` — replace `todo/` with vault authenticate/unlock, required-item
  read, item upsert, readback verification, and lock/cleanup tasks.
- `ansible/scripts/lab-run.sh` — distinguish seed and Vault modes; enforce the cutover
  marker and mandatory vault preflight.
- `ansible/tasks/load-user-vars.yml` — remove normal-operation file/env fallback for
  application secrets and merge the in-memory vault values into the runtime contract.
- `ansible/tasks/bootstrap/write-generated-facts.yml` — write non-secret registry facts
  only; reject secret-shaped fields.
- `rundeck/bootstrap-rundeck.sh` and maintenance jobs — deploy Caddy then Vaultwarden,
  enroll, import and verify seed secrets, cut over, remove seed files, then continue.
- `ansible/playbooks/maintenance/` — add an explicit Vaultwarden recovery/bootstrap path;
  do not add a job that restores secrets to `facts.yml`.
- `rundeck/bootstrap-rundeck.sh` — make its secret files temporary bootstrap inputs and
  stage the separate machine-unlock credential in Key Storage.
- `rundeck/jobs/*.yaml`, `semaphore/project.json` — provide the machine credential to the
  job process and make the vault preflight common to every deploy.
- roles and wiring tasks that currently read credential fields from
  `config/.generated/facts.yml` — consume the in-memory vault contract instead.
- `ansible/vars/CONTRACT.md` — split non-secret generated facts from the secret runtime
  contract and name every canonical vault item and field.
- `.claude/CLAUDE.md`, `.claude/specs/secrets-handling.md`, runner documentation — state
  the seed/cutover model and the hard deployment dependency accurately.

## Acceptance

- [ ] A fresh runner uses its seed files only to deploy and initialize Vaultwarden
- [ ] Bootstrap imports every seed secret, verifies each by readback, records cutover, and
      removes all seed files before deploying the next service
- [ ] Interrupting bootstrap before cutover is resumable and does not delete the only copy
      of a seed secret
- [ ] After cutover, stopping Vaultwarden causes every deploy to fail in preflight before
      making any infrastructure or service change
- [ ] After cutover, recreating a seed file does not make an ordinary deploy bypass
      Vaultwarden
- [ ] The explicit recovery workflow is the only path that can re-enter seed mode
- [ ] `config/.generated/facts.yml` contains no token, password, private key, API key, API
      secret, or other credential value
- [ ] Every generated or rotated secret is present in its canonical Vaultwarden item before
      the producing job can succeed
- [ ] Rebuilding the runner and supplying only the vault machine-access credential restores
      access to all platform secrets without redeploying or rotating a service
- [ ] No secret or vault session appears in a job log, artifact, diff, Ansible cache, or
      managed-guest metadata
- [ ] Documentation describes Vaultwarden as a mandatory runtime dependency, not a
      durability mirror or optional restore source

## Decided

- **No Vaultwarden means no deploy.** There is no offline or cache-backed mode after
  cutover.
- **Seed files are temporary.** They are verified into Vaultwarden and then removed; they
  are not durable credential stores.
- **`facts.yml` is non-secret only.** It can cache topology and registry data, but never a
  value whose disclosure grants access.
- **Secrets move through memory.** Normal jobs read them from and write them to Vaultwarden;
  they do not restore a secret-bearing local cache.
- **Recovery is explicit.** Vaultwarden bootstrap/recovery may use seed material; ordinary
  deploys may not.

## Decisions

- **Machine authentication:** Bitwarden CLI personal API-key login plus master-password
  unlock for a dedicated automation account; three individually encrypted job secrets.
- **Cutover marker:** `/etc/homelab-infra/state/vault-mode`.
- **Break glass:** confirmation-gated recovery job and
  `rundeck/VAULTWARDEN-RECOVERY.md`; no implicit fallback.
- **Write compensation:** PBS revokes a newly created token when vault persistence fails;
  generate-before-apply producers store and verify before mutation.
- **Rotation.** Item storage makes rotation possible but does not schedule it; automated
  rotation remains out of scope for this slice.
