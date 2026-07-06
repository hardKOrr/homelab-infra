# reconcile-config-example

**Type:** refactor

**Depends on:** implement-config-loader (meta slice 001 — the loader in `ansible/tasks/load-user-vars.yml` is now authoritative and final for this item)

**Spec:** `.claude/meta/002-reconcile-config-example/README.md` (the slice this promotes); `ansible/vars/CONTRACT.md` §2/§4/§5/§6; `.claude/specs/config-layering.md`

## Goal

Reconcile the four user-facing config surfaces with the landed loader (slice 001) and the contract (slice 000): document each example file's landing point in `homelabinfra_config` in its own comments, settle the `vaultwarden:` placement in the example's header, remove the `networks:` null subtree from `homelabinfra-defaults.yml`, and sync `vars/user-vars-example.yml` to the contract. Examples-and-defaults change only — no loader, playbook, task, or role logic is touched.

## Context

### The loader is final — reconcile *to* it, not around it

`ansible/tasks/load-user-vars.yml` (landed by `implement-config-loader`, read it as ground truth) merges four layers, low → high, all `combine(recursive=True)`:

1. `vars/homelabinfra-defaults.yml` — unwraps the `homelabinfra_defaults:` wrapper key; seed.
2. `config/proxmox.yml` — **no wrapper**; its top-level keys (`proxmox:`, `networks:`, `ansible:`) inject directly at the top of `homelabinfra_config`.
3. `config/infrastructure.yml` — **no wrapper**; the whole file lands under `homelabinfra_config.infrastructure`.
4. `user_vars_file` (legacy back-compat) — the file self-wraps in `homelabinfra_config:`; wins on conflict.

Because layers merge recursively, **an uncommented empty value in a user-layer file silently blanks the git-managed default underneath it** — this is the core config-layering rule (`.claude/specs/config-layering.md`): "Optional keys appear commented out, never as empty values." That rule is what most of this item enforces on the example files.

### Current state of the four files (ground truth, verified this session)

**`config.example/proxmox.yml`** — top-level `proxmox:` (`host`, `port`, `node`, `api_user`, `api_token_id`, `api_token_secret`), `networks:` (`default:` with `cidr`/`gateway`/`dns_servers`/`bridge`/`vlan`/`ip_offset`/`max_hosts`, plus a commented-out `iot:` VLAN example), `ansible:` (`ssh_user: root`, `ssh_public_key: ""`). Structurally correct for the loader (keys land at `homelabinfra_config.proxmox.*` / `.networks.*` / `.ansible.*`). What it lacks is *documentation of that landing point* — the meta-002 acceptance. **`host`/`port` are the stale key names; slice 004 owns the rename — do not rename them here.** Today `playbooks/bootstrap.yml:41-42` asserts `homelabinfra_config.proxmox.host`, so the example's `host` is currently self-consistent; renaming here would break bootstrap and is explicitly out of scope.

**`config.example/infrastructure.yml`** — top-level `domain:`, `reverse_proxy:`, `sso:`, `notifications:`, `dns:`, `backups:`, `vaultwarden:`. Header comment (lines 6-8) declares the doctrine "This file declares ROLES and PROVIDER CHOICES only — not IPs or tokens. Connection details are written automatically to config/.generated/facts.yml". Two blocks contradict that doctrine as written: `dns.host: "192.168.1.1"` (an IP — legitimate because external DNS hosts are *not in the Proxmox inventory*, the file's own §DNS comment already explains this) and `vaultwarden.admin_token` (a token — legitimate because of the bootstrap chicken-and-egg: Vaultwarden is the secrets store, so its own admin token cannot live in Vaultwarden; CLAUDE.md "Secrets" documents `VAULTWARDEN_ADMIN_TOKEN` as one of exactly two secrets outside Vaultwarden, accepted here or as env var).

**Decision already made upstream — do not reopen:** `vaultwarden:` **stays in `infrastructure.yml`**. `CONTRACT.md` §2 lists `vaultwarden:` among this file's top-level keys and §5 lists `vaultwarden.admin_token` as a **required key of this file**; CLAUDE.md "Secrets" says the token is "written to `config/infrastructure.yml` after bootstrap step 1". A separate `config/secrets.yml` would contradict the landed contract and add a third user file. The meta-002 acceptance line "`vaultwarden:` placement decision is documented in the example file's comment header" is satisfied by *amending the header doctrine* to name its two exceptions (external-host IPs like `dns.host`; the Vaultwarden admin token) and *why*, so the file no longer contradicts itself.

**`ansible/vars/homelabinfra-defaults.yml`** — wraps everything in `homelabinfra_defaults:`. Line 2 is the null subtree `networks:` (key with no value). `CONTRACT.md` §6 assigns this violation to this slice: "remove null subtree (use `{}` or omit)". The only consumer of `homelabinfra_config.networks` is `ansible/tasks/network/generate-ip.yml`, which already guards the point of use with a friendly assert (`lines 12-15`: `networks is defined` / `networks[network_name] is defined` / `.cidr is defined`, `fail_msg: "Required inputs: homelabinfra_config.networks[<name>].cidr"`) and line 20 already defends with `homelabinfra_config.networks.default | default({})`. So **no new assert is needed** — the config-layering rule's "assert required subtrees with a friendly fail_msg at the point of use" is already satisfied; this item only removes the null. Meta-002's "populate `networks:` with a real default" alternative is rejected: defaults cannot know the user's subnet, `config.example/proxmox.yml` already ships a fully-worked `networks.default`, and `CONTRACT.md` §5 marks `networks.<name>.*` as required user input. Note the file also carries an unrelated `#TODO: EVerything about VM stuff` comment (line 21) — leave it; not this item's scope.

**`ansible/vars/user-vars-example.yml`** — the *legacy* `user_vars_file` example: self-wrapped in `homelabinfra_config:`, uses the **canonical** `api_host`/`api_port` names (already ahead of `config.example/proxmox.yml`; keep them — do not downgrade to `host`/`port`).

**Decision already made upstream — keep the file, sync it.** The contract keeps the `user_vars_file` back-compat path (§2 row 7, §4 step 4); both runner READMEs (`semaphore/README.md:26`, `rundeck/README.md:26`) document wrapping invocations in `ansible/scripts/with-proxmox-env.sh <user-vars.yml> …`, and this file carries the authoritative comment block (lines 12-16) explaining the `PROXMOX_API_*` env-var mechanism for the dynamic inventory — deleting it would orphan that documented workflow. "Sync" means:

- **Preserve**: the `homelabinfra_config:` wrapper, the canonical `api_host`/`api_port` key names, and the `PROXMOX_API_*` / `with-proxmox-env.sh` comment block verbatim in meaning.
- **Fix the config-layering violations** — uncommented empty values that blank git-managed defaults on merge: `ansible.ssh_user: ""` (blanks default `root`), `proxmox.api_port: ""` (blanks default `8006`), `proxmox.api_user: ""` (blanks default `root@pam`, defined at `homelabinfra-defaults.yml:7`). The operative rule is general — *every* key with a git-managed default, not a fixed list. Per the rule, keys with git-managed defaults appear commented out, never as empty values. Keys in wholly-user-owned subtrees (`networks.<name>.*` — no default subtree exists after this item) may show real placeholder values; empty-string placeholders for required keys that have *no* default (`api_host`, `api_token_id`, `api_token_secret`, `ssh_public_key`) are acceptable since there is nothing to blank.
- **`docker_hosts.docker_default_host.type: "vm"`** duplicates the default verbatim — harmless but teaches overriding; `homelabinfra_config.docker_hosts` *is* read (`playbooks/docker/create-docker-host.yml:18-26`). Groomer decides: comment it out with a note, or keep as a worked example.
- **`apps.docker_example.docker_tag`** — **no code reads `homelabinfra_config.apps`** (verified by grep this session); per-app config is the *separate* `app_config` merge (`CONTRACT.md` "App-level layering note", slice 005). It is an orphan key teaching a shape the code ignores. Groomer decides: remove, or comment out with a pointer to `config/apps/<instance>.yml`.

### Landing-point documentation (the meta-002 headline acceptance)

Each of the two `config.example/*.yml` files gains comments stating where its keys land, matching `CONTRACT.md` §2 exactly:

- `config.example/proxmox.yml`: top-level `proxmox:` / `networks:` / `ansible:` are injected as-is at the top of `homelabinfra_config` (→ `homelabinfra_config.proxmox.*`, `.networks.*`, `.ansible.*`).
- `config.example/infrastructure.yml`: the whole file lands under `homelabinfra_config.infrastructure.*` (e.g. `domain` → `homelabinfra_config.infrastructure.domain`).

A file-header note (or per-block notes — groomer's call on placement) is sufficient; the point is a user or maintainer can trace every top-level key to its `homelabinfra_config` path without opening the loader. Every uncommented key in both files must appear in `CONTRACT.md` §5's required/optional tables — if the sweep finds a key that does not, that is a contract drift to surface, not silently paper over (none is expected; the §5 tables were written from these same files).

### Out of scope — owned elsewhere, do not touch

- **`proxmox.host`/`port` → `api_host`/`api_port` rename** anywhere (examples, bootstrap assert, defaults) → **slice 004** (this item unblocks it; meta-002 "Blocks: 004").
- **`config/apps/<instance>.yml` schema** and the `app_config` merge → **slice 005**.
- **Loader changes** (`load-user-vars.yml`) — final per slice 001; this item edits no `.yml` under `ansible/tasks/` or `ansible/playbooks/`.
- **CLAUDE.md / README prose** about the doctrine — the meta acceptance targets the example file's own header only.

### Gate reality (for Verification)

The only gates are `lint` and `test` in `.claude/build.yml` (thin wrappers over `.claude/gate/lint.sh` / `test.sh`, run via WSL). Both are static: ansible-lint over `playbooks/`/`roles/`/`tasks/`/`vars/` YAML, and `--syntax-check` over playbooks. Neither executes the runtime merge, and **neither gate parses `config.example/*.yml`** (outside the lint targets) — but `ansible/vars/*.yml` edits (defaults, user-vars-example) *are* linted. The "user copies both examples unchanged and gets a fully populated `homelabinfra_config`" acceptance is therefore proven by inspection: trace each example key through the four-layer merge above against `CONTRACT.md` §5's required list. Known pre-existing `test`-gate `[ERROR]` diagnostics (docker role missing, `instance` undefined in restart/tail playbooks, empty rollback playbook) are non-fatal and unrelated — see `.claude/plans/done/implement-config-loader.md` Run log; a Bash-tool "exit 1" on the gate commands has twice been confirmed a WSL relay artifact, so capture the real exit code with an explicit `; echo RC=$?` inside the WSL call.

## Acceptance criteria

- Every top-level key in `config.example/proxmox.yml` (`proxmox:`, `networks:`, `ansible:`) and `config.example/infrastructure.yml` (`domain:`, `reverse_proxy:`, `sso:`, `notifications:`, `dns:`, `backups:`, `vaultwarden:`) has an in-file comment documenting its landing point in `homelabinfra_config`, consistent with `CONTRACT.md` §2 — verifiable from the diff.
- No key is renamed in either example file: `proxmox.host` / `proxmox.port` remain as-is (slice 004 owns the rename); `user-vars-example.yml` retains canonical `api_host`/`api_port`.
- `config.example/infrastructure.yml`'s header doctrine no longer contradicts the file's content: it names its two exceptions (external-host connection details like `dns.host`; the Vaultwarden admin token, with the chicken-and-egg reason and the `VAULTWARDEN_ADMIN_TOKEN` env-var alternative), and the `vaultwarden:` block stays in this file per `CONTRACT.md` §5.
- `ansible/vars/homelabinfra-defaults.yml` contains no null subtree: the bare `networks:` (line 2) is removed (omitted or `{}` — groomer decides which and says why); no other key's value changes.
- `ansible/vars/user-vars-example.yml` is kept and synced: `homelabinfra_config:` wrapper intact, `PROXMOX_API_*` env-var comment block preserved, no uncommented empty-string/empty-list value remains on any key that has a git-managed default (`ansible.ssh_user`, `proxmox.api_port`, `proxmox.api_user` are the three known violations), and the orphan `apps:` block is removed or commented with a pointer to the `config/apps/<instance>.yml` mechanism.
- Diff scope: only `config.example/proxmox.yml`, `config.example/infrastructure.yml`, `ansible/vars/homelabinfra-defaults.yml`, `ansible/vars/user-vars-example.yml` change. No playbook, task, role, or loader file is touched.
- The `lint` and `test` gates in `.claude/build.yml` pass (same results as base; pre-existing diagnostics enumerated in Context are accepted only if identical on base).

## Plan

Four files change, all examples-and-defaults. No file under `ansible/tasks/`, `ansible/playbooks/`,
or `ansible/roles/` is touched. Apply the edits below verbatim.

### File 1 — `config.example/proxmox.yml` (landing-point header only; no key renamed, no value changed)

Replace the current header block (lines 1-4) with the header plus a landing-point note. Keep every
existing `proxmox:` / `networks:` / `ansible:` line below it byte-for-byte (including `host`/`port` —
slice 004 owns that rename).

Replace:
```yaml
---
# Proxmox connection and global network config.
# Copy this to config/proxmox.yml and fill in your values.
# This file is gitignored — git pull will never overwrite it.
```
with:
```yaml
---
# Proxmox connection and global network config.
# Copy this to config/proxmox.yml and fill in your values.
# This file is gitignored — git pull will never overwrite it.
#
# Where these keys land (see ansible/vars/CONTRACT.md §2): the loader injects the three
# top-level keys below directly into homelabinfra_config, unwrapped —
#   proxmox:  → homelabinfra_config.proxmox.*
#   networks: → homelabinfra_config.networks.*
#   ansible:  → homelabinfra_config.ansible.*
```

### File 2 — `config.example/infrastructure.yml` (landing-point note + doctrine amendment in header; body unchanged)

Replace the current header block (lines 1-8) with the version below. It (a) adds the whole-file
landing point and (b) amends the "roles and provider choices only" doctrine to name its two
deliberate exceptions so the file no longer contradicts its own `dns.host` and `vaultwarden`
blocks. The `vaultwarden:` block at the bottom stays exactly as-is (per `CONTRACT.md` §5).

Replace:
```yaml
---
# Platform infrastructure declarations.
# Copy this to config/infrastructure.yml and fill in your values.
# This file is gitignored — git pull will never overwrite it.
#
# This file declares ROLES and PROVIDER CHOICES only — not IPs or tokens.
# Connection details are written automatically to config/.generated/facts.yml
# by the bootstrap playbook after each service is deployed.
```
with:
```yaml
---
# Platform infrastructure declarations.
# Copy this to config/infrastructure.yml and fill in your values.
# This file is gitignored — git pull will never overwrite it.
#
# Where these keys land (see ansible/vars/CONTRACT.md §2): the loader places this WHOLE file
# under homelabinfra_config.infrastructure — e.g. domain → homelabinfra_config.infrastructure.domain,
# reverse_proxy.provider → homelabinfra_config.infrastructure.reverse_proxy.provider.
#
# This file declares ROLES and PROVIDER CHOICES only — not IPs or tokens — with two
# deliberate exceptions:
#   1. Connection details for hosts NOT in the Proxmox inventory (e.g. dns.host for an
#      external OPNsense) — homelab-infra cannot resolve them, so you supply the IP here.
#   2. vaultwarden.admin_token — Vaultwarden IS the secrets store, so its own admin token
#      cannot live inside it (bootstrap chicken-and-egg). Paste it here after bootstrap
#      step 1, or supply it as the VAULTWARDEN_ADMIN_TOKEN env var.
# Everything else (connection details for managed services) is written automatically to
# config/.generated/facts.yml by the bootstrap playbook after each service is deployed.
```

### File 3 — `ansible/vars/homelabinfra-defaults.yml` (remove the null subtree)

Change line 2 only. Replace the bare `networks:` (null value) with an explicit empty mapping.

Replace:
```yaml
homelabinfra_defaults:
  networks:
  ansible:
```
with:
```yaml
homelabinfra_defaults:
  networks: {}
  ansible:
```
No other line changes. Leave the `#TODO: EVerything about VM stuff` comment on the `vm:` line
untouched (out of scope).

### File 4 — `ansible/vars/user-vars-example.yml` (sync to contract; wrapper + env-var comment preserved)

Three targeted edits. The `homelabinfra_config:` wrapper (line 1), the `networks:` block
(lines 5-11), and the `PROXMOX_API_*` / `with-proxmox-env.sh` comment block (lines 12-16) stay
exactly as they are. Canonical `api_host`/`api_port` names are kept — do not downgrade.

**Edit 4a — `ansible:` block.** `ssh_user: ""` blanks the git-managed default `root`; comment it
out. `ssh_public_key` has no default, so its empty placeholder is safe — keep it.

Replace:
```yaml
  ansible:
    ssh_user: ""
    ssh_public_key: ""
```
with:
```yaml
  ansible:
    # ssh_user: root         # optional — defaults to 'root' (homelabinfra-defaults.yml); uncomment only to override
    ssh_public_key: ""       # required — paste your public key; no default, so empty here blanks nothing
```

**Edit 4b — `proxmox:` block.** `api_port: ""` blanks default `8006` and `api_user: ""` blanks
default `root@pam`; comment both out. `api_host`, `api_token_id`, `api_token_secret`, `node` are
required with no default — empty placeholders are safe, keep them. Leave the `lxc:`/`vm:` subkeys
below unchanged (no defaults blanked; `vmid: 0` is an auto-allocate sentinel, not a blanking value).

Replace:
```yaml
  proxmox:
    api_host: ""
    api_port: ""
    api_user: ""
    api_token_id: ""
    api_token_secret: ""
    node: ""
```
with:
```yaml
  proxmox:
    api_host: ""             # required — no default
    # api_port: 8006         # optional — defaults to 8006 (homelabinfra-defaults.yml); uncomment only to override
    # api_user: root@pam     # optional — defaults to root@pam (homelabinfra-defaults.yml); uncomment only to override
    api_token_id: ""         # required — no default
    api_token_secret: ""     # required — no default
    node: ""                 # required — no default
```

**Edit 4c — `docker_hosts:` kept as worked example, `apps:` orphan removed.** Keep the
`docker_hosts` block (it is read by `playbooks/docker/create-docker-host.yml`; `type: "vm"` is a
real, non-empty value that mirrors the default and demonstrates the override point — it blanks
nothing) and add a clarifying comment. Remove the `apps:` block entirely (no code reads
`homelabinfra_config.apps`; per-app config is the separate `app_config` merge) and replace it with a
pointer comment that does **not** teach a shape slice 005 has not settled.

Replace (note the trailing space after `docker_hosts:` in the current file):
```yaml
  docker_hosts: 
    docker_default_host:
      type: "vm"
  apps:
    docker_example:
      docker_tag: "docker_default_host"
```
with:
```yaml
  docker_hosts:
    docker_default_host:
      type: "vm"           # example override (mirrors the default) — read by playbooks/docker/create-docker-host.yml
  # Per-app config does NOT live in this file. It merges separately (app_config) from
  # config/apps/<instance>.yml layered over vars/app-defaults/<app>.yml — see CONTRACT.md
  # "App-level layering note"; the per-instance schema is owned by meta slice 005.
```

### Test-first note

This change touches no executable code — there is no unit under test to write. The "test" is the
static-gate run plus the inspection merge-trace in `## Verification`. The implementer applies the
edits above exactly, then runs both gates and records the merge-trace result in the Run log.

## Decisions

- **(a) `networks:` in `homelabinfra-defaults.yml` — `{}` vs omit → `{}`.** `.claude/specs/config-layering.md`
  blesses both ("use `{}` or omit the key"). Chose `{}` because it keeps the seed structurally
  type-stable: `homelabinfra_config.networks` is *always* a mapping, so the consumer
  `ansible/tasks/network/generate-ip.yml` — which reads `homelabinfra_config.networks.default | default({})`
  (line 20) and asserts `networks is defined` then `networks[network_name] is defined` (lines 12-14) —
  operates on a known dict rather than an undefined var, and a recursive combine of `{}` with the
  user's `config/proxmox.yml` `networks` layer is a guaranteed no-op (never blanks). Omitting would
  leave `networks` undefined until the user layer supplies it, subtly shifting which of the two
  friendly asserts fires on a missing-subnet mistake. No new assert is added (the point-of-use
  assert already satisfies the spec's "assert required subtrees" clause).
- **(b) Landing-point comment placement → file header (not per-block).** Each of the two
  `config.example/*.yml` files has few top-level keys with a single consistent landing rule
  (proxmox: three keys inject as-is; infrastructure: whole file lands under one path). A compact
  header note states every top-level key → its `homelabinfra_config` path in one place, so a user
  traces any key without opening the loader, while the existing per-block comments stay focused on
  semantics. Per-block notes would scatter the same fact and bloat the diff.
- **(c) `docker_hosts.docker_default_host.type: "vm"` in `user-vars-example.yml` → keep as worked
  example.** It duplicates the default verbatim, but `type: "vm"` is a real, non-empty value that
  blanks nothing on merge (it is the config-layering *empty/`0`* rule that bans blanking, not real
  overrides), and `homelabinfra_config.docker_hosts` *is* read by
  `playbooks/docker/create-docker-host.yml`. Kept with a clarifying comment naming the consumer and
  that it mirrors the default — it teaches the real override point for a key the code actually reads.
- **(d) Orphan `apps:` block in `user-vars-example.yml` → remove, replace with a pointer comment
  (no fake shape).** No code reads `homelabinfra_config.apps` (per-app config is the separate
  `app_config` merge; `CONTRACT.md` "App-level layering note"), so the key is inert clutter in the
  merge. Removed rather than commented-out-verbatim because the commented shape
  (`apps.docker_example.docker_tag`) is a schema slice 005 has not settled and would teach a wrong
  mechanism. Replaced with a comment that answers "where does per-app config go?" by pointing at
  `config/apps/<instance>.yml` and the contract's layering note, without asserting a schema.
- **`proxmox.api_user: ""` in `user-vars-example.yml` is a THIRD blanking violation, folded in.**
  The dossier enumerated only `ansible.ssh_user` and `proxmox.api_port` as "the two known
  violations," but `homelabinfra-defaults.yml` line 7 defines `proxmox.api_user: root@pam`, so the
  example's `api_user: ""` blanks it identically. The acceptance criterion is written generally
  ("any key that has a git-managed default"), so this is in scope and Edit 4b comments it out with
  the others. Surfaced as a thin-projection note for korr-design (the enumeration undercounted).

## Verification

### Static gates (the only real gates — `.claude/build.yml`)

Run both, capturing the real exit code inside WSL (a Bash-tool "exit 1" on these has twice been a
WSL relay artifact — see Context):

- `lint`: `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/lint.sh; echo RC=$?'`
- `test`: `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/test.sh; echo RC=$?'`

**What the gates actually prove:** `lint` runs ansible-lint over `playbooks/`/`roles/`/`tasks/`/`vars/`,
so it *does* parse the two edited `ansible/vars/*.yml` files — it proves `homelabinfra-defaults.yml`
(`networks: {}`) and `user-vars-example.yml` remain lint-clean YAML with no *new* diagnostics vs base.
`test` runs `--syntax-check` over playbooks; it touches neither `vars/` nor `config.example/`.
**Neither gate parses `config.example/*.yml`** (outside the lint targets) and **neither executes the
runtime four-layer merge.** Accepted pre-existing `test`-gate `[ERROR]` diagnostics (docker role
missing; `instance` undefined in `restart-app`/`tail-applog` playbooks; empty `rollback-container`
playbook) must appear **identically on base** — a diagnostic that is new or gone is a regression to
investigate, per `.claude/plans/done/implement-config-loader.md` Run log.

### Inspection (proves what the static gates cannot)

The "user copies both examples unchanged → fully populated `homelabinfra_config`" acceptance and the
no-blanking rule are proven by a merge-trace against `ansible/vars/CONTRACT.md` §5, not by any
command:

1. **Landing-point comments match `CONTRACT.md` §2.** In the diff, confirm `config.example/proxmox.yml`
   documents `proxmox`/`networks`/`ansible` injecting as-is into `homelabinfra_config`, and
   `config.example/infrastructure.yml` documents the whole file landing under
   `homelabinfra_config.infrastructure`.
2. **Every uncommented top-level/leaf key in both example files appears in `CONTRACT.md` §5.** Trace
   proxmox.yml (`proxmox.host`/`port`/`node`/`api_user`/`api_token_id`/`api_token_secret`;
   `networks.default.cidr`/`gateway`/`dns_servers`/`bridge`/`vlan`/`ip_offset`/`max_hosts`;
   `ansible.ssh_user`/`ssh_public_key`) and infrastructure.yml (`domain`; `reverse_proxy.*`; `sso.*`;
   `notifications.*`; `dns.provider`/`host`; `backups.datastore_path`/`schedule`/`retention`;
   `vaultwarden.admin_token`/`instance`). All are present in §5 (host/port named there as the
   pre-slice-004 aliases of `api_host`/`api_port`) — if any is absent, that is contract drift to
   surface, not paper over. None expected.
3. **No blanking value survives in `user-vars-example.yml`.** After the edits, no uncommented
   empty-string/empty-list value remains on any key that has a git-managed default. The three keys
   with defaults — `ansible.ssh_user` (`root`), `proxmox.api_port` (`8006`), `proxmox.api_user`
   (`root@pam`) — are commented out; the wholly-user-owned `networks.<name>.*` placeholders and the
   no-default required keys (`api_host`, `api_token_id`, `api_token_secret`, `node`, `ssh_public_key`)
   remain as empty placeholders (nothing to blank). The `homelabinfra_config:` wrapper, canonical
   `api_host`/`api_port` names, and the `PROXMOX_API_*`/`with-proxmox-env.sh` comment block are intact.
4. **`homelabinfra-defaults.yml` has no null subtree** — line 2 is `networks: {}`; no other value
   changed; the `#TODO` comment is untouched.
5. **`config.example/infrastructure.yml` header no longer self-contradicts** — it names the two
   exceptions (external-host IPs like `dns.host`; the Vaultwarden admin token with the
   chicken-and-egg reason and the `VAULTWARDEN_ADMIN_TOKEN` env-var alternative), and the
   `vaultwarden:` block still lives in this file.
6. **Diff scope** — `git status`/`git diff --name-only` shows exactly four changed files:
   `config.example/proxmox.yml`, `config.example/infrastructure.yml`,
   `ansible/vars/homelabinfra-defaults.yml`, `ansible/vars/user-vars-example.yml`. No playbook, task,
   role, or loader file appears.

### korr-qa senior pass confirms before commit

Both gates green with pre-existing diagnostics identical to base; the six inspection points above
hold on the diff; no key renamed; diff scope is exactly the four files.

## Run log

<!-- example shape (delete when the first real round lands):
### round 1
[implementer] what changed, files touched, notes for the reviewer; may flag "(decision needed: …)"
 - test: `pytest -q` → exit 0 (14 passed)
 - lint: `ruff check .` → exit 0
[reviewer] verdict: CHANGES
 - finding: what failed, where, why — actionable without re-reviewing

### round 2
[implementer] what changed in response
 - test: `pytest -q` → exit 0 (15 passed)
[reviewer] verdict: PASS
 - fixed in place: typo in docs/usage.md (no decision involved)
[qa] verdict: PASS
 - senior notes; any agent-surfaced notification lifted here
-->

### round 1
[implementer] Applied all four edits from `## Plan` verbatim; no deviations, no decisions needed.

Files touched:
- `config.example/proxmox.yml` — header (lines 1-4 → 1-10): added landing-point note documenting
  `proxmox:`/`networks:`/`ansible:` inject as-is into `homelabinfra_config.*`. Body (host/port and
  everything below) untouched byte-for-byte.
- `config.example/infrastructure.yml` — header (lines 1-8 → 1-18): added whole-file landing-point
  note (`homelabinfra_config.infrastructure`) and amended the doctrine comment to name its two
  exceptions (external-host IPs like `dns.host`; `vaultwarden.admin_token` with the chicken-and-egg
  reason and the `VAULTWARDEN_ADMIN_TOKEN` env-var alternative). Body (including the `vaultwarden:`
  block) untouched.
- `ansible/vars/homelabinfra-defaults.yml` — line 2 only: bare `networks:` (null) → `networks: {}`.
  No other line changed; `#TODO: EVerything about VM stuff` left untouched.
- `ansible/vars/user-vars-example.yml` — three edits: (4a) `ansible.ssh_user: ""` commented out with
  a default-pointer note, `ssh_public_key` kept as required-empty; (4b) `proxmox.api_port: ""` and
  `proxmox.api_user: ""` commented out with default-pointer notes, `api_host`/`api_token_id`/
  `api_token_secret`/`node` kept as required-empty with inline notes; (4c) `docker_hosts` kept
  (trailing space after the key removed as part of the verbatim replace block) with a clarifying
  comment naming its consumer, `apps:` orphan block removed and replaced with a pointer comment to
  `config/apps/<instance>.yml` / `app_config` / CONTRACT.md's "App-level layering note". Wrapper,
  `networks:` block, and the `PROXMOX_API_*`/`with-proxmox-env.sh` comment block (lines 12-16)
  preserved verbatim.

No file under `ansible/tasks/`, `ansible/playbooks/`, or `ansible/roles/` touched.

Gate evidence (run via WSL per build.yml, real exit code captured with `; echo RC=$?` inside the
WSL call per the plan's noted relay hazard):

- lint: `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/lint.sh; echo RC=$?'`
  → `RC=0`. Output tail: `Passed: 0 failure(s), 0 warning(s) in 55 files processed of 64
  encountered. Profile 'min' was required, but 'production' profile passed.`
- test: `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/test.sh; echo RC=$?'`
  → `RC=0`. Exactly three `[ERROR]` diagnostics present, all pre-existing per the plan's Context:
  `docker` role not found (`playbooks/docker/create-docker-host.yml`), `'instance' is undefined`
  (`maintenance/restart-app.yml` and `maintenance/tail-applog.yml`, two occurrences), and `Empty
  playbook, nothing to do: .../stacks/rollback-container.yml`. Verified identical on base: stashed
  the four-file diff, re-ran the test gate on the unmodified tree (`git stash` / re-run / `git stash
  pop`), got the same four `[ERROR]` lines verbatim — no regression, no new diagnostic, none
  resolved.

Inspection (Verification points 1-6):

1. Landing-point comments match `CONTRACT.md` §2 — confirmed: `proxmox.yml` header states
   `proxmox`/`networks`/`ansible` inject as-is; `infrastructure.yml` header states the whole file
   lands under `homelabinfra_config.infrastructure`.
2. Every uncommented key in both example files traced against `CONTRACT.md` §5 — all present
   (`proxmox.host`/`port` named there as the pre-slice-004 aliases of `api_host`/`api_port`;
   `networks.default.*`; `ansible.*`; `domain`; `reverse_proxy.*`; `sso.*`; `notifications.*`;
   `dns.provider`/`host`; `backups.*`; `vaultwarden.admin_token`/`instance`). No contract drift
   found.
3. No blanking value survives in `user-vars-example.yml` — the three keys with git-managed
   defaults (`ansible.ssh_user`, `proxmox.api_port`, `proxmox.api_user`) are commented out;
   `networks.<name>.*` placeholders and no-default required keys (`api_host`, `api_token_id`,
   `api_token_secret`, `node`, `ssh_public_key`) remain as empty placeholders. Wrapper, canonical
   `api_host`/`api_port` names, and the `PROXMOX_API_*`/`with-proxmox-env.sh` comment block intact.
4. `homelabinfra-defaults.yml` line 2 is `networks: {}`; no other value changed; `#TODO` comment
   untouched.
5. `infrastructure.yml` header no longer self-contradicts — the two exceptions (external-host IPs;
   Vaultwarden admin token with chicken-and-egg reason and env-var alternative) are named; the
   `vaultwarden:` block still lives in this file.
6. Diff scope — `git status` shows exactly the four target files modified (plus my own run-lock
   write at `.claude/autobuild/run.json`, which is out of the plan's scope by design). No playbook,
   task, role, or loader file appears in the diff.

No decisions needed — every choice in `## Decisions` matched the plan's edits exactly (including
the "third blanking violation" note for `proxmox.api_user`, which the plan had already folded into
Edit 4b). Nothing to flag as blocked. Working tree left uncommitted for the reviewer.

[reviewer] verdict: PASS
 - Re-ran both gates independently: lint → RC=0, "Passed: 0 failure(s), 0 warning(s) in 55 files
   processed of 64 encountered" (matches implementer's evidence verbatim). test → RC=0, the same
   four `[ERROR]` diagnostics (docker role not found; `instance` undefined ×2 in
   restart-app/tail-applog; empty rollback-container playbook) — matches implementer's evidence
   and the plan's Context enumeration of accepted pre-existing diagnostics.
 - Diff scope verified via `git diff --name-only`: exactly `config.example/proxmox.yml`,
   `config.example/infrastructure.yml`, `ansible/vars/homelabinfra-defaults.yml`,
   `ansible/vars/user-vars-example.yml`, plus this plan file. No task/playbook/role/loader file
   touched. (`.claude/settings.local.json` also shows modified but is pre-existing/out-of-scope
   per the task framing, not part of this change.)
 - No key renamed: `config.example/proxmox.yml` keeps `host`/`port` byte-for-byte; body below the
   new header comment is untouched. `user-vars-example.yml` keeps canonical `api_host`/`api_port`.
 - `config.example/infrastructure.yml` header now names its two doctrine exceptions (external-host
   IPs, `vaultwarden.admin_token` with chicken-and-egg reasoning + env-var alternative); the
   `vaultwarden:` block is untouched and still present per CONTRACT.md §5.
 - `ansible/vars/homelabinfra-defaults.yml`: only line 2 changed, bare `networks:` → `networks: {}`;
   confirmed via diff no other line moved.
 - `ansible/vars/user-vars-example.yml`: wrapper, `networks:` block, and the `PROXMOX_API_*`/
   with-proxmox-env.sh comment block preserved verbatim; all three blanking violations
   (`ansible.ssh_user`, `proxmox.api_port`, `proxmox.api_user`) now commented out with
   default-pointer notes; orphan `apps:` block removed and replaced with a pointer comment to
   `config/apps/<instance>.yml` / CONTRACT.md's "App-level layering note" that asserts no schema.
 - Cross-checked landing-point comments and required/optional keys against `CONTRACT.md` §2 and §5
   directly (not just the plan's paraphrase) — both example files' top-level keys all trace
   cleanly; no contract drift found.
 - Reflexes: no secrets introduced (comments/placeholders only), no injection surface, no
   swallowed errors — none applicable, this diff is comments + one scalar value + a block removal.
 - No trivia fixes needed; diff matched the plan's edits exactly.

[qa] verdict: PASS
 - Senior pass: read the full four-file diff myself against the plan's verbatim blocks and
   CONTRACT.md §2/§5 — exact match, no key renamed, no value changed beyond the three
   commented-out blanking violations and `networks: {}`. The `docker_hosts:` trailing-space
   removal is part of the plan's own replace block, not a deviation.
 - Gate evidence accepted without a third run: implementer and reviewer ran both gates
   independently with verbatim-matching output (lint RC=0 clean; test RC=0 with only the
   enumerated pre-existing diagnostics, verified identical on base via stash/re-run). A
   comments+scalar diff cannot alter what these static gates parse beyond the two linted
   vars files, which stayed clean.
 - Nothing lifted: no `(decision needed: …)` flags, no reviewer notes requiring resolution.
   Meta slice 002's acceptance boxes are all satisfied by this diff; slice 004 (key rename)
   is now unblocked.
 - Committing: four config-surface files + this plan (moved to done/), squashed on
   `refactor/reconcile-config-example`, ff-merged to master.
