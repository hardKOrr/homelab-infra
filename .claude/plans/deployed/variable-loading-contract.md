# variable-loading-contract

**Type:** refactor

**Depends on:** —

**Spec:** `.claude/meta/000-variable-loading-contract/README.md` (the slice this promotes);
`.claude/specs/config-layering.md`; `.claude/specs/namespace-merge-discipline.md`;
`.claude/architecture.md` ("Variable namespaces" + "config/.generated/facts.yml" seams)

## Goal

Write the authoritative variable-loading contract — a Markdown spec, **no code** — at
`ansible/vars/CONTRACT.md`. It fixes, in one place, the three namespaces, exactly what loads from
which file into which key, the single canonical shape of `homelabinfra_infra`, the merge
precedence, and the required-vs-optional keys per config file. Every conflict between this contract
and the current code/examples is enumerated with the downstream slice (001/002/003/004/005/200)
that resolves it, so no later slice has to guess the intended shape.

## Context

The CLAUDE.md story ("copy `config.example/*.yml` → `config/`, run bootstrap") is not implemented,
and the code that *would* consume that config reads several mutually incompatible shapes. This
slice writes the contract that unblocks 001–004; it changes no runtime behavior. All source facts
below are projected here so the groomer and implementer need not re-read the code.

### The three namespaces (authoritative restatement)

- `homelabinfra_config.*` — merged user + defaults, the **input** layer.
- `homelabinfra_instance.*` — facts **computed at runtime** (IP allocation, vmid, etc.).
- `homelabinfra_infra.*` — the **service registry**: provider choices + endpoints + tokens, loaded
  from `config/.generated/facts.yml`.

### What exists today (the ground truth to contract against)

- **`ansible/tasks/load-user-vars.yml`** (the only loader) knows *nothing* about `config/*.yml`. It
  (1) asserts `user_vars_file is defined or homelabinfra_config is defined`, (2) `include_vars`
  loads `vars/homelabinfra-defaults.yml` (top-level wrapper `homelabinfra_defaults:`), (3)
  optionally `include_vars` loads a legacy single `user_vars_file` (which itself wraps everything in
  `homelabinfra_config:` — see `vars/user-vars-example.yml`), (4) sets
  `homelabinfra_config: "{{ homelabinfra_defaults | default({}) | combine(homelabinfra_config |
  default({}), recursive=True) }}"`. So **nothing reads `config/proxmox.yml` or
  `config/infrastructure.yml`** — the documented workflow has no code path.
- **`config.example/proxmox.yml`** has unwrapped top-level keys: `proxmox:` (`host`, `port`, `node`,
  `api_user`, `api_token_id`, `api_token_secret`), `networks:` (named subnets with `cidr`, `gateway`,
  `dns_servers`, `bridge`, `vlan`, `ip_offset`, `max_hosts`), `ansible:` (`ssh_user`,
  `ssh_public_key`).
- **`config.example/infrastructure.yml`** has unwrapped top-level keys: `domain:`, `reverse_proxy:`
  (`provider`, `instance`), `sso:` (`provider`, `instance`), `notifications:` (`provider`,
  `instance`, opt `topic`/`webhook_url`), `dns:` (`provider`, `host`, opt `api_key`/`instance`),
  `backups:` (`datastore_path`, `schedule`, `retention`), `vaultwarden:` (`admin_token`, `instance`).
- **`ansible/vars/homelabinfra-defaults.yml`** wraps everything in `homelabinfra_defaults:` and uses
  `proxmox.api_port` / `proxmox.api_user` (note: **`api_port`, not `port`**) plus `proxmox.lxc.*`,
  `proxmox.vm.*`, `docker_hosts.*`. It also carries a **null subtree** `networks:` (no value) —
  a config-layering violation (git-managed defaults must contain no null subtrees).
- **`config/.generated/facts.yml`** is loaded straight into `homelabinfra_infra`
  (`_template.yml:47`, `create-docker-host.yml:99`, `restart-app.yml:36`) via
  `include_vars … name: homelabinfra_infra`. It is written by
  `tasks/bootstrap/write-generated-facts.yml`, which today is a **TODO stub** — its header comment
  documents yet another shape.

### The central defect: `homelabinfra_infra` is read in incompatible shapes

Three shapes exist in the repo at once, and the contract must collapse them to one:

- **Shape B — role-keyed (what live wiring reads, and the README proposes):**
  `homelabinfra_infra.domain`, `.reverse_proxy.provider`, `.sso.provider`, `.dns.provider`
  (`_template.yml:129,136–150`). Keyed by *role*, provider-agnostic. This is the shape most code
  already depends on.
- **Shape A leak — flat pre-built URL:** `homelabinfra_infra.notifications.ntfy_url` +
  `.notifications.topic` (`check-native-updates.yml:54`, `restart-app.yml:40`,
  `guest-bootstrap.yml:86`). Stores a *built URL* (`ntfy_url`) rather than a `host`, and is
  ntfy-specific rather than provider-agnostic — inconsistent with Shape B.
- **Stub shape — service/function-keyed:** the `write-generated-facts.yml` header comment sketches
  `vaultwarden:{url,admin_token}`, `caddy:{admin_api_url}`, `authentik:{api_url,api_token}`,
  `uptime_kuma:{api_url,api_token}`, `notifications:{ntfy_url,topic}` — keyed by service name, not
  role. Non-binding (it's a TODO), owned by slice 200.

The README (item 3) proposes the canonical registry as **Shape B**, provider-agnostic, seeded from
`infrastructure.yml`'s provider choices and grown per-service by `write-generated-facts.yml`:

```yaml
domain: homelab.example.com          # copied from infrastructure.yml
reverse_proxy: { provider, instance, host, port }
sso:           { provider, instance, host, token }
notifications: { provider, instance, host, topic }
dns:           { provider, host, api_key }
backups:       { instance, datastore_path }
vaultwarden:   { host, port }        # populated after bootstrap step 1
```

Note the relationship the contract must make explicit: `infrastructure.yml` feeds **two** places —
its provider *choices* merge into `homelabinfra_config.infrastructure` (input layer, available at
provision time), and those same choices plus bootstrap-written endpoints/tokens land in
`homelabinfra_infra` (the registry read at wiring time). They are not the same dict.

### Merge order (README item 4), precedence low → high

1. `vars/homelabinfra-defaults.yml` (unwrap `homelabinfra_defaults:`) → seed of `homelabinfra_config`.
2. `config/proxmox.yml` (no wrapper in the file; loader injects `proxmox`/`networks`/`ansible` under
   `homelabinfra_config`).
3. `config/infrastructure.yml` (loader injects under `homelabinfra_config.infrastructure`).
4. `user_vars_file` if present — back-compat; already carries its own `homelabinfra_config:` wrapper.

App-level layering (`vars/app-defaults/<app>.yml` → `config/apps/<instance>.yml` → `app_config`) is
a *separate* merge done per-play in the template, not part of `homelabinfra_config`; the contract
should describe it but keep it distinct.

### Known conflicts the contract must enumerate and assign (README acceptance)

The README is explicit: conflicts are **enumerated and pointed at their resolving slice**, never
left open. At minimum:

- **Proxmox key names** — examples use `proxmox.host`/`proxmox.port`; defaults + architecture use
  `api_host`/`api_port`; config-layering names `api_host`/`api_port` canonical → **slice 004**.
- **`config.example` wrapper mismatch** — unwrapped top-level keys vs the namespaces the code reads
  → **slices 001 (loader) + 002 (reconcile examples)**.
- **`notifications.ntfy_url` vs `notifications.host`** — Shape-A leak; contract mandates
  `host`+`topic` (consumers build `{{ host }}/{{ topic }}`) → resolution owned by **slice 200**
  (defines what bootstrap writes) with the three consumers (`check-native-updates.yml`,
  `restart-app.yml`, `guest-bootstrap.yml`) flagged for alignment.
- **`write-generated-facts.yml` stub shape** — service-keyed sketch superseded by the canonical
  Shape B → **slice 200**.
- **Instance/app config schema** — `config/apps/<instance>.yml` shape is contradictory elsewhere →
  **slice 005** (name it, don't resolve here).
- **`networks:` null subtree** in defaults → **slice 002** (config-layering: no null subtrees).

### Doc location

Per the README, the contract lives at **`ansible/vars/CONTRACT.md`** — next to the files it
governs, so Ansible authors and downstream slices cite one concrete data-shape reference. The
`.claude/specs/config-layering.md` inspection-rule spec stays as-is but gains a one-line pointer to
it so the two never drift.

## Acceptance criteria

- New file **`ansible/vars/CONTRACT.md`** exists and contains all five README deliverables as
  distinct, non-empty sections: (1) the three namespaces, (2) a file → wrapper → target-key load
  map covering `homelabinfra-defaults.yml`, `config/proxmox.yml`, `config/infrastructure.yml`,
  `vars/app-defaults/<app>.yml`, `config/apps/<instance>.yml`, `config/.generated/facts.yml`, and
  the back-compat `user_vars_file`, (3) the canonical `homelabinfra_infra` shape, (4) the merge
  order low→high, (5) required-vs-optional keys per config file.
- The doc defines **exactly one** canonical `homelabinfra_infra` shape (role-keyed, Shape B); it
  does not present two competing shapes as both valid.
- Every conflict listed in Context ("Known conflicts") appears in the doc, each naming the
  resolving slice (001/002/003/004/005/200) — no conflict is left as an open, unassigned question.
- `.claude/specs/config-layering.md` gains a one-line pointer to `ansible/vars/CONTRACT.md`.
- **Doc-only change:** no `.yml`, playbook, role, or task file is modified. The `lint` and `test`
  gates from `.claude/build.yml` pass unchanged (a Markdown addition under `ansible/vars/` engages
  neither ansible-lint nor `--syntax-check`).

## Plan

Doc-only change. Two files are touched, **no `.yml`/playbook/role/task file is edited**:

1. **Create** `ansible/vars/CONTRACT.md` (new authoritative spec).
2. **Edit** `.claude/specs/config-layering.md` — add one pointer line.

There is no test harness for a Markdown file, so "test-first" here means: the acceptance criteria
are the checklist, and the doc is written to satisfy each one section-by-section. The `## Verification`
section below is the proof. The implementer transcribes the content specified here **verbatim** —
every shape decision is already resolved in `## Decisions`; the implementer chooses nothing.

### Step 1 — Create `ansible/vars/CONTRACT.md`

The file has a short title/intro line then **five required sections** (matching the five README
deliverables in the acceptance criteria) plus a sixth "Known conflicts" section and a distinct
app-level-layering note. Write exactly the content below.

**Intro (top of file):** a one-sentence statement that this is the authoritative variable-loading
contract for `homelabinfra_*` namespaces and config files, that it is the single data-shape
reference downstream slices cite, and a back-pointer line: "Inspection rules that protect these
shapes: `.claude/specs/config-layering.md` and `.claude/specs/namespace-merge-discipline.md`."

**Section 1 — "The three namespaces".** Three bullets, the authoritative restatement from Context:
- `homelabinfra_config.*` — merged user + defaults, the **input** layer (available at provision time).
- `homelabinfra_instance.*` — facts **computed at runtime** (IP allocation, vmid, etc.).
- `homelabinfra_infra.*` — the **service registry**: provider choices + endpoints + tokens, loaded
  from `config/.generated/facts.yml` (read at wiring time).
State explicitly (one line) that `homelabinfra_config.infrastructure` and `homelabinfra_infra` are
**not the same dict**: `infrastructure.yml` feeds both — its provider *choices* merge into
`homelabinfra_config.infrastructure` (input layer), and those choices plus bootstrap-written
endpoints/tokens land in `homelabinfra_infra` (the registry).

**Section 2 — "Load map: file → wrapper → target key".** A Markdown table with columns
`File | Top-level wrapper in file | Loaded into | Notes`, one row per source, covering all seven
sources the acceptance criteria enumerate:

| File | Wrapper in file | Loaded into | Notes |
|---|---|---|---|
| `vars/homelabinfra-defaults.yml` | `homelabinfra_defaults:` | `homelabinfra_config` (seed, lowest precedence) | unwrapped before merge |
| `config/proxmox.yml` | none (top-level `proxmox:`, `networks:`, `ansible:`) | `homelabinfra_config` (loader injects those three keys) | **not yet wired in loader → slice 001** |
| `config/infrastructure.yml` | none (top-level `domain:`, `reverse_proxy:`, `sso:`, `notifications:`, `dns:`, `backups:`, `vaultwarden:`) | `homelabinfra_config.infrastructure` | **not yet wired in loader → slice 001** |
| `vars/app-defaults/<app>.yml` | none | `app_config` (per-play app merge — see app-layering note) | separate merge, not part of `homelabinfra_config` |
| `config/apps/<instance>.yml` | none | `app_config` (per-play app merge) | schema contradictory today → slice 005 |
| `config/.generated/facts.yml` | none | `homelabinfra_infra` (whole file, via `include_vars … name: homelabinfra_infra`) | written by `write-generated-facts.yml` (TODO stub → slice 200) |
| `user_vars_file` (back-compat) | `homelabinfra_config:` | `homelabinfra_config` | legacy single-file path; already self-wrapping |

**Section 3 — "Canonical `homelabinfra_infra` shape".** One sentence: "There is exactly one shape —
role-keyed, provider-agnostic (Shape B). Consumers build derived values (e.g. a notification URL)
from `host` + `topic`; the registry never stores pre-built URLs." Then this YAML block verbatim:

```yaml
# config/.generated/facts.yml, loaded whole into homelabinfra_infra
domain: homelab.example.com          # copied from infrastructure.yml
reverse_proxy: { provider, instance, host, port }
sso:           { provider, instance, host, token }
notifications: { provider, instance, host, topic }   # NOT ntfy_url — consumers build {{ host }}/{{ topic }}
dns:           { provider, host, api_key }
backups:       { instance, datastore_path }
vaultwarden:   { host, port }        # populated after bootstrap step 1
```

Immediately after the block, one line each naming the two superseded shapes so no slice re-adopts
them: "Superseded — do not use: (a) Shape-A flat pre-built URL `notifications.ntfy_url` +
`.notifications.topic` (read today by `check-native-updates.yml`, `restart-app.yml`,
`guest-bootstrap.yml`) — reconciled by slice 200; (b) the service/function-keyed stub sketch in
`write-generated-facts.yml`'s header comment (`vaultwarden:{url,admin_token}`, `caddy:{admin_api_url}`,
…) — superseded by slice 200."

**Section 4 — "Merge order (low → high precedence)".** An ordered list, exactly:
1. `vars/homelabinfra-defaults.yml` (unwrap `homelabinfra_defaults:`) → seed of `homelabinfra_config`.
2. `config/proxmox.yml` (loader injects `proxmox`/`networks`/`ansible` under `homelabinfra_config`).
3. `config/infrastructure.yml` (loader injects under `homelabinfra_config.infrastructure`).
4. `user_vars_file` if present (back-compat; already carries its own `homelabinfra_config:` wrapper).
Note under the list: all merges use `combine(recursive=True)`; later layers win per key.

**Section 5 — "Required vs optional keys per config file".** Two tables (one per user-facing config
file). Use columns `Key | Required? | Default / notes`.

For `config/proxmox.yml`:
- `proxmox.api_host` — required (canonical name; examples' `host` is a conflict → slice 004).
- `proxmox.api_port` — optional, default `8006` (canonical name; examples' `port` → slice 004).
- `proxmox.node` — required.
- `proxmox.api_user` — required.
- `proxmox.api_token_id` — required.
- `proxmox.api_token_secret` — required (secret).
- `networks.<name>.cidr` — required per named subnet.
- `networks.<name>.gateway` — required per named subnet.
- `networks.<name>.dns_servers` — required per named subnet.
- `networks.<name>.bridge` — required per named subnet.
- `networks.<name>.vlan` — optional.
- `networks.<name>.ip_offset` — optional.
- `networks.<name>.max_hosts` — optional.
- `ansible.ssh_user` — required.
- `ansible.ssh_public_key` — required.

For `config/infrastructure.yml`:
- `domain` — required.
- `reverse_proxy.provider` — required (`caddy | nginx | none`).
- `reverse_proxy.instance` — required unless provider `none`.
- `sso.provider` — required (`authentik | none`).
- `sso.instance` — required if provider `authentik`, else optional.
- `notifications.provider` — required (`ntfy | gotify | discord | none`).
- `notifications.instance` — required unless provider `none`.
- `notifications.topic` — optional.
- `notifications.webhook_url` — optional.
- `dns.provider` — required (`pihole | adguard | opnsense | none`).
- `dns.host` — required for external providers (not in Proxmox inventory).
- `dns.api_key` — optional.
- `dns.instance` — optional.
- `backups.datastore_path` — required.
- `backups.schedule` — optional.
- `backups.retention` — optional.
- `vaultwarden.admin_token` — required (secret; written after bootstrap step 1).
- `vaultwarden.instance` — optional.

Under the second table add one line: the required/optional split for `config/.generated/facts.yml`
follows the canonical shape in Section 3 but its authoritative required-key list is owned by
**slice 200** (it defines what bootstrap writes); the `config/apps/<instance>.yml` schema is owned
by **slice 005**. Contract names them here, does not resolve them.

**Section 6 — "Known conflicts and owning slices".** A table with columns
`Conflict | Contract's canonical decision | Resolving slice`, one row per Context conflict — all six
must appear:

| Conflict | Canonical decision | Slice |
|---|---|---|
| Proxmox key names: examples `proxmox.host`/`.port` vs defaults/arch `api_host`/`api_port` | `api_host` / `api_port` canonical | **004** |
| `config.example/*.yml` unwrapped top-level keys vs namespaces the code reads | loader injects namespaces (001); examples reconciled to match (002) | **001 + 002** |
| `notifications.ntfy_url` (Shape-A leak) vs `notifications.host` + `.topic` | registry stores `host` + `topic`; consumers build the URL; three consumers flagged for alignment | **200** |
| `write-generated-facts.yml` stub service-keyed sketch vs canonical Shape B | Shape B supersedes the stub sketch | **200** |
| `config/apps/<instance>.yml` schema contradictory across repo | named, not resolved here | **005** |
| `networks:` null subtree in `homelabinfra-defaults.yml` (config-layering violation) | remove null subtree (use `{}` or omit) | **002** |

**App-level layering note (distinct, kept separate).** One short paragraph: the per-app merge
(`vars/app-defaults/<app>.yml` → `config/apps/<instance>.yml` → `app_config`) is a **separate**
per-play merge done in the app template, **not** part of `homelabinfra_config`. It is described here
for completeness but governed by its own precedence; do not conflate it with the four-layer
`homelabinfra_config` merge in Section 4.

### Step 2 — Add the pointer in `.claude/specs/config-layering.md`

Insert one line immediately after the opening paragraph (after the existing line
"Users only write what differs; everything else falls through."):

```
The authoritative data-shape contract these rules protect — namespaces, load map, the canonical
`homelabinfra_infra` shape, merge order, and per-file required keys — lives at
`ansible/vars/CONTRACT.md`; keep the two in sync.
```

Change nothing else in that file. Do not touch `namespace-merge-discipline.md`, any `.yml`, or any
playbook/role/task.

## Decisions

- **D1 — Canonical `homelabinfra_infra` is Shape B (role-keyed, provider-agnostic).** Adopted as the
  single shape because live wiring already reads it (`_template.yml:129,136–150`) and the README
  (item 3) proposes it. Role-keyed + provider-agnostic means adding a provider does not reshape the
  registry. The doc presents exactly one shape and explicitly marks the other two as superseded, so
  no downstream slice re-adopts a dead shape.
- **D2 — `notifications.ntfy_url` is a Shape-A leak; contract mandates `host` + `topic`.** The
  registry stores connection primitives (`host`, `topic`), not a pre-built ntfy-specific URL;
  consumers build `{{ host }}/{{ topic }}`. This keeps the registry provider-agnostic and consistent
  with every other role entry. The actual code reconciliation (rewriting what bootstrap writes and
  aligning the three consumers `check-native-updates.yml`, `restart-app.yml`, `guest-bootstrap.yml`)
  is **not** done in this doc-only item — it is assigned to slice 200 and the consumers are flagged.
- **D3 — Conflicts are enumerated-and-assigned here; their code resolution is deferred to the owning
  slice.** Per the README's acceptance, the contract's job is to name every conflict and point it at
  the slice that fixes it (001/002/003/004/005/200), never to leave one open and never to fix code
  here. All six Context conflicts get a row in Section 6.
- **D4 — Doc lives at `ansible/vars/CONTRACT.md`.** Per the README: next to the files it governs, so
  Ansible authors and downstream slices cite one concrete data-shape reference. Not under
  `.claude/specs/` (that tree holds inspection *rules*, which stay and merely gain a pointer).
- **D5 — Canonical Proxmox key names are `api_host`/`api_port`.** Matches `config-layering.md`'s
  stated canonical ("one key name per concept … `api_host`/`api_port` — meta slice 004") and the
  defaults file (`api_port`). The examples' `host`/`port` are recorded as the conflict resolved by
  slice 004. The contract documents the canonical target so slice 004 has an unambiguous end state.
- **D6 — App-level layering is documented but kept distinct from the `homelabinfra_config` merge.**
  Per Context, the `app-defaults → config/apps → app_config` merge is a separate per-play merge; the
  README wants it described but not folded into the four-layer `homelabinfra_config` precedence. It
  gets its own note, and the load map marks those two files as feeding `app_config`, not
  `homelabinfra_config`.
- **D7 — `config-layering.md` pointer is inserted after the opening paragraph, not in "Enforced by".**
  Placed with the intro so a reader meets the cross-reference before the rules, keeping the two docs
  from drifting. It is one line, adds no rule, and leaves the "Rule"/"Enforced by" sections
  byte-identical.
- **D8 — Required-vs-optional for `facts.yml` and `apps/<instance>.yml` is named, not fully
  specified.** Those schemas are owned by slices 200 and 005 respectively; pinning their full
  required-key lists here would pre-empt those slices and risk contradicting them. The contract states
  the canonical *shape* (Section 3) and defers the authoritative required-key list to the owning
  slice — consistent with D3.
- **D9 — Superseded shapes are named in-doc, not silently dropped.** Listing Shape-A and the stub
  sketch as "do not use" (with their current read sites) is what stops a later slice from
  reintroducing them; a contract that only shows the winner leaves the losers looking merely
  undocumented. This directly serves the acceptance line "does not present two competing shapes as
  both valid."

## Verification

This is a doc-only change; the substantive verification is by inspection (korr-qa senior pass reading
the doc against the cited real code), because neither build gate meaningfully exercises a `.md` file.

**Gates (the only two in `.claude/build.yml`; both must stay green, unchanged).** Run each and read
the exit code from the Bash tool's reported status (per the shell-relay note in `build.yml`, `$?`
does not survive the relay):

- **lint** — `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'`
  ansible-lint targets `playbooks/`/`roles/`/`tasks/`/`vars/` YAML; a Markdown file under
  `ansible/vars/` is not YAML and engages no rule. Expect the same result as the base branch (no new
  failures/warnings attributable to this change).
- **test** — `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/test.sh'`
  `--syntax-check` runs over playbooks only; a `.md` addition is invisible to it. Any pre-existing
  failures (e.g. `restart-app.yml`, `tail-applog.yml`, `rollback-container.yml`, as recorded in
  `.claude/plans/done/`) are accepted only if they reproduce identically on the base branch and none
  are caused by this change. No `.yml` was touched, so the gate result must be byte-for-byte the base
  result.

**Diff-verifiable acceptance (korr-qa confirms from the diff alone):**
- `ansible/vars/CONTRACT.md` is a new file and contains all five required sections as distinct,
  non-empty sections: (1) three namespaces, (2) file → wrapper → target-key load map covering all
  seven sources, (3) canonical `homelabinfra_infra` shape, (4) merge order low→high, (5)
  required-vs-optional keys per config file.
- The doc presents **exactly one** canonical `homelabinfra_infra` shape (role-keyed, Shape B) and
  marks Shape-A / the stub sketch as superseded — it does not present two competing shapes as valid.
- Every one of the six Context conflicts appears in Section 6 with an owning slice
  (001/002/003/004/005/200); none is left open/unassigned.
- `.claude/specs/config-layering.md` gained exactly one pointer line to `ansible/vars/CONTRACT.md`
  and is otherwise unchanged.
- `git diff --stat` shows **only** `ansible/vars/CONTRACT.md` (added) and
  `.claude/specs/config-layering.md` (one line) — no `.yml`, playbook, role, or task file modified.

**Inspection checklist (senior pass reads each documented shape against the cited real code and
confirms the contract matches ground truth where it claims to, and correctly flags where it
diverges):**
- Section 3's Shape B matches what live wiring reads at `ansible/roles/_template-*`/app template
  (`_template.yml:129,136–150` per Context) — `homelabinfra_infra.reverse_proxy.provider`, `.sso`,
  `.dns`, `.domain`.
- The `notifications.ntfy_url` "superseded" callout matches the three real read sites
  (`check-native-updates.yml:54`, `restart-app.yml:40`, `guest-bootstrap.yml:86`) and correctly
  frames them as flagged-for-alignment (slice 200), not as the contract's shape.
- The load map's wrapper/target columns match the loader's actual behavior described in Context
  (`load-user-vars.yml`: defaults unwrap `homelabinfra_defaults:`; `user_vars_file` self-wraps
  `homelabinfra_config:`; `facts.yml` loaded whole into `homelabinfra_infra`) and correctly notes
  that `config/proxmox.yml`/`config/infrastructure.yml` are **not yet wired** (slice 001).
- The Proxmox key-name rows use `api_host`/`api_port` (matching defaults + `config-layering.md`
  canonical) and Section 6 correctly assigns the examples' `host`/`port` mismatch to slice 004.
- The `networks:` null-subtree conflict is present and assigned to slice 002, matching
  `config-layering.md`'s "no null subtrees" rule.

## Run log

### [implementer] round 1

Doc-only change, exactly the two files the plan names. No decisions to make — transcribed Step 1 and
Step 2 content verbatim.

**Files touched:**
- Created `ansible/vars/CONTRACT.md` — intro + six sections (three namespaces; load map table
  covering all seven sources; canonical `homelabinfra_infra` Shape B + superseded-shapes callout;
  merge order; two required/optional-keys tables for `config/proxmox.yml` and
  `config/infrastructure.yml`; six-row known-conflicts table) + the app-level-layering note, all
  transcribed verbatim from the plan's Step 1.
- Edited `.claude/specs/config-layering.md` — inserted the exact three-line pointer from Step 2
  immediately after "Users only write what differs; everything else falls through."; nothing else in
  that file changed (confirmed via `git diff`: 4 insertions, 0 deletions).

`git diff --stat` on the two intended files:
```
.claude/specs/config-layering.md | 4 ++++
1 file changed, 4 insertions(+)
```
plus `ansible/vars/CONTRACT.md` as a new untracked file. No `.yml`, playbook, role, or task file
touched. (`.claude/settings.local.json` and `.claude/agents/fable-groomer.md` show as
modified/untracked in `git status` but predate this round — not touched by this implementation.)

**Run-lock / branch:** wrote `.claude/autobuild/run.json` (`plan: variable-loading-contract`,
`branch: refactor/variable-loading-contract`, `base: master`) and cut `refactor/variable-loading-contract`
from `master` (fresh start — no prior branch or run log entry existed).

**Gate path adjustment:** `.claude/build.yml`'s `lint`/`test` commands `cd` into
`/mnt/c/Users/korr/source/repos/homelab-infra`, which does not exist on this machine. Ran both gates
with the `cd` target adjusted to this checkout's real WSL mount,
`/mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra`, per the launch instructions. `.claude/build.yml`
itself was not edited.

**Additional environment gap found (beyond the `cd` mismatch):** both gate scripts also hardcode
`ANSIBLE_CONFIG=/mnt/c/Users/korr/source/repos/homelab-infra/ansible/ansible.cfg` (a nonexistent path
on this machine — Ansible just falls back to its "world writable directory" warning and proceeds
without that cfg, which both gate scripts' own comments already anticipate as a known false-failure
risk for role-using playbooks) and both invoke
`"$HOME/.venvs/homelab-ansible/bin/ansible-lint"` / `.../ansible-playbook`, which did not exist in
this WSL distro (no prior bootstrap had been run here). System-wide `ansible-lint` 26.1.1 and
`ansible-playbook` were already present on PATH, so I created
`~/.venvs/homelab-ansible/bin/{ansible-lint,ansible-playbook}` as symlinks to the system binaries
(environment-only change, no repo file touched) so the gate scripts could run exactly as committed.
Neither of these two gaps is caused by this doc-only change; both are pre-existing to the WSL
environment itself.

**Gate evidence:**

- `lint` — `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/lint.sh'`
  → **exit 0**. Tail: `Passed: 0 failure(s), 0 warning(s) in 55 files processed of 64 encountered.
  Profile 'min' was required, but 'production' profile passed.`
  Compared against a temporary `master` worktree run with the same adjusted `cd`: master shows
  `55 files processed of 63 encountered` (identical pass, one fewer file encountered — exactly the
  new `CONTRACT.md`, which ansible-lint encounters but does not process, a non-YAML file under
  `ansible/vars/`). Same result as base branch; no new failures attributable to this change.

- `test` — `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/test.sh'`
  → **exit 0**. `Found 11 playbook(s) to syntax-check.` Three pre-existing `[ERROR]` lines appear
  (`playbooks/docker/create-docker-host.yml` — role `docker` not found; `restart-app.yml` and
  `tail-applog.yml` — `'instance' is undefined` for the templated `hosts:`; plus
  `stacks/rollback-container.yml` — empty playbook) but none affect the script's exit code (these
  `--syntax-check` errors are non-fatal for this ansible-core version; `rc` is only set from the
  per-playbook exit status, which stays 0). Re-ran the identical gate against a temporary `master`
  worktree: byte-for-byte the same `[ERROR]`/`[WARNING]` lines (path prefixes differing only by
  worktree directory name) and the same exit 0. These are the pre-existing failures already recorded
  in `.claude/plans/done/`; confirmed unrelated to this doc-only change.

Completed cleanly — no decision needed, not blocked. Working tree left uncommitted for the reviewer.

### [reviewer] round 1

[reviewer] verdict: PASS

**Gate evidence:** accepted as recorded, no re-run. The reasoning is sound and independently
verifiable: `.claude/build.yml`'s `lint`/`test` commands only exercise YAML under
`playbooks/`/`roles/`/`tasks/`/`vars/` (lint) and playbooks via `--syntax-check` (test); a `.md`
file under `ansible/vars/` is invisible to both. The implementer's master-worktree comparison
(lint: 55 processed/64 encountered on both branches, delta is exactly the new non-YAML file;
test: byte-for-byte identical pre-existing `[ERROR]` lines and exit 0 on both) is internally
consistent and sufficient; re-running would add nothing here.

**Acceptance criteria — verified against the diff:**
- `ansible/vars/CONTRACT.md` (new, read in full) contains all five required sections as distinct
  non-empty sections plus the sixth known-conflicts table and the separate app-level-layering
  note — content matches the plan's Step 1 verbatim, line for line.
- Exactly one canonical `homelabinfra_infra` shape presented (Shape B, role-keyed); Shape-A
  (`notifications.ntfy_url`) and the service-keyed stub sketch are explicitly named "Superseded —
  do not use," not offered as alternatives.
- All six Context conflicts appear in Section 6, each with an owning slice (004; 001+002; 200;
  200; 005; 002) — none left open.
- `.claude/specs/config-layering.md` diff is exactly the 3-line pointer inserted after "Users only
  write what differs; everything else falls through." — confirmed via `git diff master --
  .claude/specs/config-layering.md`; nothing else in that file changed.
- `git diff --stat master` shows only the plan's own run-log growth and the 4-line
  config-layering.md insertion as tracked changes, plus untracked `ansible/vars/CONTRACT.md`. No
  `.yml`, playbook, role, or task file touched. `.claude/settings.local.json` and
  `.claude/agents/fable-groomer.md` are pre-existing working-tree state, correctly excluded from
  this implementation's scope per the launch note.

**Spot-checks against real code (ground truth):**
- Shape B matches live wiring exactly: read `ansible/playbooks/apps/_template.yml` — line 129
  `homelabinfra_infra.domain`, lines 136/139/142/148 `.reverse_proxy.provider`, `.sso.provider`,
  `.dns.provider`. Matches Section 3 verbatim.
- The three `notifications.ntfy_url` read sites are exactly as claimed: grepped the repo and
  confirmed `check-native-updates.yml:54`, `restart-app.yml:40`, `guest-bootstrap.yml:86` all read
  `homelabinfra_infra.notifications.ntfy_url` — no fourth site exists, no claimed site is wrong.
- `api_host`/`api_port` canonical claim checked against `.claude/specs/config-layering.md:21`
  ("canonical: `api_host`/`api_port`") and real usage in `lxc-create.yml`/`vm-create.yml`/
  `user-vars-example.yml` — consistent. `homelabinfra-defaults.yml` carries `api_port: 8006` only
  (no `host` key), consistent with "optional, default 8006."
- `load-user-vars.yml` read directly: confirms the load map's description of defaults-unwrap,
  `user_vars_file` self-wrap, and `homelabinfra_config` merge — no divergence from Section 2/4.
- `write-generated-facts.yml` TODO-stub header comment matches the "service/function-keyed stub
  sketch" description (vaultwarden/caddy/authentik/uptime_kuma/notifications keys) cited as
  superseded.
- `networks:` null subtree confirmed present at `homelabinfra-defaults.yml:2` (key with no value),
  matching the Section 6 row.

**Reflexes:** no secrets, no injection, no swallowed errors — doc-only change, nothing applies.

**Findings:** none. No trivia fixed (none found).

### [qa] round 1

[qa] verdict: PASS

Senior pass — read `ansible/vars/CONTRACT.md` in full and confirmed against acceptance criteria:
all five required sections present + non-empty, plus the sixth Known-conflicts table and the
distinct app-layering note; exactly one canonical `homelabinfra_infra` shape (Shape B, role-keyed)
with Shape-A (`notifications.ntfy_url`) and the service-keyed stub sketch both marked "Superseded —
do not use"; all six Context conflicts appear in Section 6 each with an owning slice (004; 001+002;
200; 200; 005; 002); the load map covers all seven sources; both required/optional key tables
present. The `.claude/specs/config-layering.md` pointer is a clean 4-line insertion after the
opening paragraph (`git diff master`), nothing else in that file changed.

Gate evidence accepted (not re-run): a `.md` file under `ansible/vars/` engages neither
ansible-lint's YAML targets nor `--syntax-check`; both implementer and reviewer confirmed identical
results against a temporary `master` worktree (lint exit 0, test exit 0 with the same three
pre-existing unrelated `[ERROR]`s). Diff scope confirmed clean: only `ansible/vars/CONTRACT.md`
(new) and `.claude/specs/config-layering.md` (pointer) as this change's content — no
`.yml`/playbook/role/task touched.

Note (not a blocker, out of scope for this doc-only plan): `.claude/build.yml`'s gate commands
hardcode a WSL path `/mnt/c/Users/korr/source/repos/homelab-infra` that does not exist in this
checkout; the implementer ran the gates against the correct mount by adjusting the `cd`. A future
plan should reconcile `build.yml`'s path to this working copy so gate commands run unmodified.

Committing: `ansible/vars/CONTRACT.md` + `.claude/specs/config-layering.md` + this plan file (moved
to done/), squashed on `refactor/variable-loading-contract`, ff-merged to master.
