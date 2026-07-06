# filter-proxmox-module-params

**Type:** fix

**Depends on:** variable-loading-contract (meta 000 — the `homelabinfra_*` contract in `ansible/vars/CONTRACT.md` is landed and final)

**Spec:** `.claude/meta/003-filter-proxmox-module-params/README.md` (the slice this promotes); `ansible/vars/CONTRACT.md` §1/§5; `.claude/specs/namespace-merge-discipline.md`

## Goal

Stop splatting the raw merged config dict into `community.proxmox.proxmox` / `proxmox_kvm`: in
`ansible/tasks/proxmox/lxc-create.yml` and `ansible/tasks/proxmox/vm-create.yml`, build the
module-args dict from an **explicit allowlist** of known module parameters (meta 003's approach A —
decided, do not reopen), so user-facing bookkeeping keys (`network`, `ip_address`, `ansible_host`,
anything a user adds) can never reach the module as a bogus argument. Fold in two latent
module-call defects found while projecting this item: the missing required `api_user` argument, and
the wrong `features` value shape in defaults.

## Context

### The defect — dict splat forwards every key as a module argument

Both create tasks build `homelabinfra_instance.lxc` / `.vm` by combining a small explicit dict with
the **entire** `homelabinfra_config.proxmox.lxc` / `.vm` subtree, then splat that dict into the
module:

- `ansible/tasks/proxmox/lxc-create.yml:18-29` — explicit dict (`state`, `api_host`, `api_port`,
  `api_token_id`, `api_token_secret`, `node`, `pubkey`) `| combine(homelabinfra_config.proxmox.lxc,
  recursive=True)`; line 75: `community.proxmox.proxmox: "{{ homelabinfra_instance.lxc }}"`.
- `ansible/tasks/proxmox/vm-create.yml:16-31` — explicit dict (`state`, `api_host`, `api_port`,
  `api_token_id`, `api_token_secret`, `node`, `sshkeys`, `ciuser`) `| combine(homelabinfra_config.proxmox.vm,
  recursive=True)`; line 79: `community.proxmox.proxmox_kvm: "{{ homelabinfra_instance.vm }}"`.

Any key in the config subtree that is not a module parameter is forwarded as one, and the module's
argument-spec validation rejects it at runtime ("Unsupported parameters"). Known non-module keys
that **do** flow in today:

- `proxmox.lxc.network` / `proxmox.vm.network` — the named-subnet **reference** consumed by the
  calling playbooks (`playbooks/proxmox/create-lxc.yml:18`, `create-vm.yml:17`:
  `network_name: "{{ homelabinfra_config.proxmox.lxc.network | default('default') }}"`). It is
  user-facing: `ansible/vars/user-vars-example.yml` ships `lxc: { …, network: "default" }`. Hits
  the module as `network=default` → rejected.
- `proxmox.lxc.ip_address` / `proxmox.vm.ip_address` — injected by the calling playbooks after IP
  generation (`create-lxc.yml:20-22`, `create-vm.yml:19-21`) for downstream use; not a module param
  of either module.
- `proxmox.vm.ansible_host` — user-facing in `user-vars-example.yml`; consumed only as
  `homelabinfra_instance.vm_ansible_host` (`vm-create.yml:30`); not a module param.
- Anything a user adds to their config (e.g. `notes_for_humans: "foo"`) or we add to
  `vars/homelabinfra-defaults.yml` for bookkeeping.

Meta 003's verdict: this "works" only because current test inputs happen to be valid module params.
It blocks any real provisioning run.

### Two latent module-call defects found this session — in scope, fix here

1. **`api_user` is never passed.** Every `community.proxmox` module requires `api_user` (token auth
   is `api_user` + `api_token_id` + `api_token_secret`). Neither explicit dict includes it and the
   config subtrees don't carry it, so the module call fails "missing required arguments: api_user"
   before any filtering question even arises. The value exists: `homelabinfra_config.proxmox.api_user`,
   default `root@pam` (`ansible/vars/homelabinfra-defaults.yml:7`), listed required in
   `CONTRACT.md` §5. Route it into both **projected module-args vars** (combine it onto the
   filtered dict), not into the `Build facts` explicit dicts — the instance dicts must stay
   byte-identical (see acceptance; adding it there would also leak `api_user` into the in-guest
   JSON dump). No new assert needed (it has a git-managed default, so it is always defined).
2. **`features` default has the wrong shape.** `ansible/vars/homelabinfra-defaults.yml:15-16` ships
   `lxc.features: {nesting: true}` (a dict). The `community.proxmox.proxmox` `features` parameter is
   `type: list, elements: str` of pct option strings (e.g. `- nesting=1`); Ansible's list
   type-check raises on a dict. Fix the default to the module's shape (`features: ["nesting=1"]`).
   User-facing shape = module shape, consistent with the contract's schema-readability goal.

### The shape of the fix (meta decision A — allowlist projection; do not restructure downstream)

`homelabinfra_instance.lxc` / `.vm` is **not only** the module-args dict — it is instance facts
with live downstream consumers that must keep seeing today's content:

- `playbooks/proxmox/create-lxc.yml:33,35` — `add_host` reads `.lxc.hostname`, `.lxc.ip_address`.
- `playbooks/proxmox/create-vm.yml:32,34` — `add_host` reads `.vm.name`, `.vm_ansible_host`.
- `playbooks/docker/create-docker-host.yml:83,85` — reads `.lxc.hostname`, `.lxc.ip_address`,
  `.vm.name`, `.vm_ansible_host`.
- `tasks/stack/find-or-create-host.yml:87` — reads `.lxc.hostname`.
- The in-guest JSON dump (`lxc-create.yml:98-106`, `vm-create.yml:102-110`) writes
  `homelabinfra_instance.lxc|vm | to_nice_json`.
- The wait tasks in both create files read `.lxc.vmid` / `.vm.vmid`.

So: **leave the `homelabinfra_instance.lxc`/`.vm` construction and the network-merge steps
unchanged** (lxc-create.yml:31-72, vm-create.yml:33-76 keep writing `netif`/`nameserver`/
`searchdomain` resp. `net`/`ipconfig`/`nameservers`/`searchdomains` into the instance dict).
Insert a projection step between the network merge and the module call: build a local module-args
var by filtering `homelabinfra_instance.lxc`/`.vm` down to the allowlisted keys (e.g.
`dict2items | selectattr('key', 'in', <allowlist>) | items2dict`), and splat **that** into the
module. Downstream consumers and the JSON dump see exactly today's dict; only the module call
changes. The projection naturally covers both defaults-supplied and playbook-injected keys, and the
network-merge keys (`netif` etc.) pass through because they are in the allowlist.

Namespace discipline (`.claude/specs/namespace-merge-discipline.md`, summarized): never bare-assign
`homelabinfra_instance` (always `combine(recursive=True)` onto the existing dict); never store
`default(omit)` inside a `set_fact`-built dict. The new module-args var is a **local** task-file
var (like the existing `lxc_ip_cidr`/`vm_net0` locals), not a namespace key — plain `set_fact` or a
task-level `vars:` block is fine for it.

Dropped keys: meta allows "dropped silently or warned". Groomer decides; a one-task `debug`/`warn`
listing the dropped key names (difference of the instance dict's keys minus the allowlist) helps a
user whose typo'd param silently vanished, but keep it to one task if adopted.

### Allowlists — pin against community.proxmox 2.0.0, verify with ansible-doc

The gate venv installs `community.proxmox:==2.0.0` (see `.claude/build.yml` bootstrap comment), so
that version's argument specs are the authority. The implementer can dump the real schemas locally:

```
wsl bash -lc '~/.venvs/homelab-ansible/bin/ansible-doc -j community.proxmox.proxmox'
wsl bash -lc '~/.venvs/homelab-ansible/bin/ansible-doc -j community.proxmox.proxmox_kvm'
```

(These are not gate commands and not in an `allow:` list — they will prompt the human once each;
expected and fine in an interactive run.) Every allowlisted key must appear in that dump; trim any
that do not.

**Minimum set the allowlist must carry** (keys the repo actively routes today — meta 003
acceptance, plus the two fixes above):

- **LXC (`community.proxmox.proxmox`)**: `state`, `api_host`, `api_port`, `api_user`,
  `api_token_id`, `api_token_secret`, `node`, `pubkey`, `vmid`, `hostname`, `ostemplate`, `cores`,
  `memory`, `tags`, `features`, `password`, `description`, `onboot`, `disk_volume`, `storage`,
  `netif`, `nameserver`, `searchdomain`.
- **VM (`community.proxmox.proxmox_kvm`)**: `state`, `api_host`, `api_port`, `api_user`,
  `api_token_id`, `api_token_secret`, `node`, `sshkeys`, `ciuser`, `vmid`, `name`, `agent`,
  `onboot`, `autostart`, `description`, `tags`, `net`, `ipconfig`, `nameservers`, `searchdomains`.

Groomer may extend each list with a **modest** set of additional params users plausibly override
(believed valid in 2.0.0, to be confirmed against the ansible-doc dump before landing): LXC —
`swap`, `unprivileged`, `ostype`, `timezone`, `timeout`, `cpuunits`, `mounts`, `mount_volumes`,
`pool`, `hookscript`, `startup`; VM — `cores`, `memory`, `sockets`, `vcpus`, `cpu`, `ostype`,
`scsihw`, `boot`, `bootdisk`, `balloon`, `bios`, `machine`, `ide`, `sata`, `scsi`, `virtio`,
`efidisk0`, `vga`, `serial`, `cipassword`, `cicustom`, `citype`, `ciupgrade`, `storage`, `pool`,
`kvm`, `numa_enabled`, `hotplug`, `startup`, `timeout`. A key not present in the dump is dropped
from the list, not worked around. Keys deliberately **not** allowlisted even though users write
them: `network`, `ip_address`, `ansible_host` — they are ours, and filtering them is the point.

Where the allowlists live: they are code, not user config — define them in the task files
themselves (a `vars:` block on the projection task or a `set_fact` immediately before it), not in
`homelabinfra-defaults.yml`. Groomer picks the exact placement.

### Out of scope — owned elsewhere, do not touch

- **Sanitizing the in-guest JSON dump / its `/root/home` path** →
  `.claude/plans/backlog/strip-secrets-from-guest-instance-json.md` (it explicitly defers the
  module-call restructure to this item; this item likewise leaves the dump reading
  `homelabinfra_instance.lxc|vm` unchanged).
- **The `password: changeme` default value** → `.claude/plans/backlog/remove-default-lxc-password.md`
  (keep `password` in the allowlist; do not change its value).
- **`host`/`port` → `api_host`/`api_port` renames in examples** → meta slice 004.
- **The network-merge `set_fact` logic** in both files (lxc-create.yml:31-72, vm-create.yml:33-76)
  and everything from the module call down (waits, JSON write) — unchanged apart from the module
  task's args source.
- **`ansible/tasks/proxmox/ip-to-vmid.yml` / `ip-to-vmid-guest.yml`** — vmid derivation happens
  before these task files run; untouched.

### Gate reality (for Verification)

The only gates are `lint` and `test` in `.claude/build.yml` (thin wrappers over
`.claude/gate/lint.sh` / `test.sh`, run via WSL). Both are static: ansible-lint over
`playbooks/`/`roles/`/`tasks/`/`vars/`, and `--syntax-check` over playbooks — the edited task files
and `homelabinfra-defaults.yml` are inside the lint targets. **Neither gate executes the module
call**, so neither can prove the filtering behavior; that is proven by inspection of the projection
expression plus the ansible-doc cross-check of every allowlisted key. Known pre-existing
`test`-gate `[ERROR]` diagnostics (docker role missing; `instance` undefined in
restart-app/tail-applog; empty rollback-container playbook) are accepted only if identical on base
— see `.claude/plans/done/reconcile-config-example.md` Run log. A Bash-tool "exit 1" on the gate
commands has twice been a WSL relay artifact: capture the real exit code with `; echo RC=$?`
inside the WSL call.

## Acceptance criteria

- `ansible/tasks/proxmox/lxc-create.yml` passes `community.proxmox.proxmox` a dict built by
  projecting onto an explicit allowlist of that module's parameters — the raw merged
  `homelabinfra_instance.lxc` is no longer splatted into the module (checkable from the diff: the
  module task's args source is the filtered var, and the allowlist is literal in the file).
- `ansible/tasks/proxmox/vm-create.yml` does the same for `community.proxmox.proxmox_kvm`.
- A non-module key in user config (e.g. `proxmox.lxc.notes_for_humans: "foo"`, or the real
  `network`, `ip_address`, `ansible_host` keys) cannot reach either module: those keys are absent
  from both allowlists, and the projection admits allowlisted keys only.
- Every key the repo routes today is in the right allowlist: LXC — `netif`, `nameserver`,
  `searchdomain`, `pubkey`, `vmid`, `hostname`, `ostemplate`, `cores`, `memory`, `tags`,
  `features`, `password`, `description`, `onboot`, `disk_volume`, `storage`, plus
  `state`/`api_host`/`api_port`/`api_user`/`api_token_id`/`api_token_secret`/`node`; VM — `net`,
  `ipconfig`, `nameservers`, `searchdomains`, `sshkeys`, `ciuser`, `vmid`, `name`, `agent`,
  `onboot`, `autostart`, `description`, `tags`, plus the same api/connection keys.
- `api_user` is routed into both module-args dicts from `homelabinfra_config.proxmox.api_user`
  (fixes the missing required argument).
- `ansible/vars/homelabinfra-defaults.yml` ships `lxc.features` in the module's `list[str]` shape
  (`["nesting=1"]`), not a dict; no other default value changes.
- Every allowlisted key exists in the community.proxmox 2.0.0 argument spec (verified against the
  `ansible-doc -j` dump from the gate venv; evidence in the Run log).
- `homelabinfra_instance.lxc` / `.vm` content is unchanged (the projection is a separate local
  var): the network-merge steps, `add_host` consumers (`create-lxc.yml`, `create-vm.yml`,
  `create-docker-host.yml`), `find-or-create-host.yml`, the vmid waits, and the in-guest JSON dump
  are untouched by the diff except for the module task's args source.
- Diff scope: only `ansible/tasks/proxmox/lxc-create.yml`, `ansible/tasks/proxmox/vm-create.yml`,
  `ansible/vars/homelabinfra-defaults.yml` change.
- The `lint` and `test` gates in `.claude/build.yml` pass (pre-existing diagnostics identical to
  base only).

## Plan

Three files change, exactly as listed in Context's "Files to touch". Apply the edit blocks below
**verbatim** — every string is anchored to unique existing text.

The shape (decided upstream — approach A): in each task file, insert **one** new `set_fact` task
between the network-merge step and the module-call task. That task builds a **local** module-args
var (`lxc_module_args` / `vm_module_args`) by projecting the already-fully-merged
`homelabinfra_instance.lxc` / `.vm` dict down to a **literal allowlist** carried in a task-level
`vars:` block, then splats **that local var** into the module. The instance-dict construction, the
network-merge steps, the vmid waits, the in-guest JSON dump, and every `add_host`/downstream
consumer stay byte-for-byte unchanged — only the module task's args source changes (raw instance
dict → filtered local var). See `## Decisions` for why each call was made.

The projection expression (both files, same shape): filter the instance dict to the allowlist, then
combine in `api_user` (which lives one level up at `homelabinfra_config.proxmox.api_user` and is
deliberately **not** merged into the instance dict — see Decision E):

```
homelabinfra_instance.<lxc|vm>
  | dict2items
  | selectattr('key', 'in', <allowlist>)
  | items2dict
  | combine({'api_user': homelabinfra_config.proxmox.api_user})
```

### Test-first note

This change touches no executable unit — there is no runtime path either gate exercises (neither
gate calls the Proxmox module; see `## Verification`). The "test" is (a) both static gates green
with pre-existing diagnostics identical to base, and (b) the `ansible-doc -j` allowlist cross-check
plus the diff-inspection points in `## Verification`. The implementer applies the three edit blocks
exactly, runs the allowlist verification step (Verification step 2), then both gates, and records
all of it in the Run log.

### Edit 1 — `ansible/tasks/proxmox/lxc-create.yml`

Insert the new "Build LXC module arguments" task immediately before the "Create LXC container" task
and change that task's args source from `homelabinfra_instance.lxc` to `lxc_module_args`. Nothing
above (assert, build-facts, both network tasks) or below (waits, JSON dump) changes.

Replace:
```yaml
- name: Create LXC container
  community.proxmox.proxmox: "{{ homelabinfra_instance.lxc }}"
  register: create_lxc_result
```
with:
```yaml
- name: Build LXC module arguments from the community.proxmox.proxmox allowlist
  ansible.builtin.set_fact:
    lxc_module_args: >-
      {{ homelabinfra_instance.lxc | dict2items
         | selectattr('key', 'in', lxc_module_keys)
         | items2dict
         | combine({'api_user': homelabinfra_config.proxmox.api_user}) }}
  vars:
    # community.proxmox.proxmox parameters only. User/bookkeeping keys (network, ip_address)
    # are absent by design so they can never reach the module. Every key here is verified
    # against the community.proxmox 2.0.0 argument spec — see this plan's Verification step 2.
    lxc_module_keys:
      - state
      - api_host
      - api_port
      - api_user
      - api_token_id
      - api_token_secret
      - node
      - pubkey
      - vmid
      - hostname
      - ostemplate
      - cores
      - memory
      - tags
      - features
      - password
      - description
      - onboot
      - disk_volume
      - storage
      - netif
      - nameserver
      - searchdomain
      - swap
      - unprivileged
      - ostype
      - timezone
      - timeout
      - cpuunits
      - mounts
      - mount_volumes
      - pool
      - hookscript
      - startup

- name: Create LXC container
  community.proxmox.proxmox: "{{ lxc_module_args }}"
  register: create_lxc_result
```

### Edit 2 — `ansible/tasks/proxmox/vm-create.yml`

Same pattern for `community.proxmox.proxmox_kvm`. Insert the "Build VM module arguments" task
immediately before "Create VM" and change its args source from `homelabinfra_instance.vm` to
`vm_module_args`. Nothing else in the file changes.

Replace:
```yaml
- name: Create VM
  community.proxmox.proxmox_kvm: "{{ homelabinfra_instance.vm }}"
  register: create_vm_result
```
with:
```yaml
- name: Build VM module arguments from the community.proxmox.proxmox_kvm allowlist
  ansible.builtin.set_fact:
    vm_module_args: >-
      {{ homelabinfra_instance.vm | dict2items
         | selectattr('key', 'in', vm_module_keys)
         | items2dict
         | combine({'api_user': homelabinfra_config.proxmox.api_user}) }}
  vars:
    # community.proxmox.proxmox_kvm parameters only. User/bookkeeping keys (network, ip_address,
    # ansible_host) are absent by design. vm_ansible_host is a sibling of .vm (not inside it), so
    # it is untouched. Every key here is verified against the 2.0.0 spec — see Verification step 2.
    vm_module_keys:
      - state
      - api_host
      - api_port
      - api_user
      - api_token_id
      - api_token_secret
      - node
      - sshkeys
      - ciuser
      - vmid
      - name
      - agent
      - onboot
      - autostart
      - description
      - tags
      - net
      - ipconfig
      - nameservers
      - searchdomains
      - cores
      - memory
      - sockets
      - vcpus
      - cpu
      - ostype
      - scsihw
      - boot
      - bootdisk
      - balloon
      - bios
      - machine
      - ide
      - sata
      - scsi
      - virtio
      - efidisk0
      - vga
      - serial
      - cipassword
      - cicustom
      - citype
      - ciupgrade
      - storage
      - pool
      - kvm
      - numa_enabled
      - hotplug
      - startup
      - timeout

- name: Create VM
  community.proxmox.proxmox_kvm: "{{ vm_module_args }}"
  register: create_vm_result
```

### Edit 3 — `ansible/vars/homelabinfra-defaults.yml`

Fix the `lxc.features` default from a dict (`{nesting: true}`, which the module's `list[str]`
type-check rejects) to the module's list-of-pct-option-strings shape. Block-list form is used to
match the file's own `tags:` block style; it is exactly equal to `["nesting=1"]`. No other line
changes; leave the `#TODO: EVerything about VM stuff` comment untouched.

Replace:
```yaml
      features:
        nesting: true
```
with:
```yaml
      features:
        - nesting=1
```

### What is deliberately NOT changed (guard rails for the implementer)

- The `Build facts …` `set_fact` in both files (`lxc-create.yml:18-29`, `vm-create.yml:16-31`) —
  untouched. `api_user` is **not** added here (that would change `homelabinfra_instance.*` content,
  which the acceptance forbids); it is injected only into the projected local via `combine`.
- Both network `set_fact` steps, both `assert`s, the vmid waits, and the in-guest JSON dumps.
- `homelabinfra_instance.lxc` / `.vm` content — byte-for-byte identical to today, so the JSON dump
  and every `add_host` / `find-or-create-host` consumer are unaffected.
- No file outside the three named above.

## Decisions

- **(A) Exact projection expression → filter the instance dict, then combine `api_user`.**
  `homelabinfra_instance.<lxc|vm> | dict2items | selectattr('key', 'in', <keys>) | items2dict |
  combine({'api_user': homelabinfra_config.proxmox.api_user})`. Chosen over building the args dict
  from scratch out of `homelabinfra_config` (meta 003's sketch): the instance dict has already
  merged all four sources (explicit connection keys + the config subtree + playbook-injected keys +
  the network-merge `netif`/`net`/`ipconfig`/etc.), so filtering it in one place covers every source
  and cannot drift from what the file actually builds. `selectattr('key', 'in', list)` is the
  standard Ansible idiom (Jinja `in` test); `dict2items`/`items2dict` round-trip with default
  `key`/`value` field names. The `combine` tail handles `api_user` (Decision E).

- **(B) Allowlist placement → a task-level `vars:` block on the same `set_fact` that builds the
  args var (not a separate `set_fact`, not `homelabinfra-defaults.yml`).** The allowlist is code,
  not user config, so it stays in the task file (Context is explicit). A task-scoped `vars:` keeps
  it non-persistent and adjacent to the projection that consumes it — one new task per file, args
  source and allowlist visible together, satisfying the acceptance's "allowlist is literal in the
  file, module task's args source is the filtered var". A dependent var in a task `vars:` block is
  in scope for that task's own parameters, so `lxc_module_args` referencing `lxc_module_keys` in the
  same task resolves correctly. Var names follow the file's existing local convention
  (`lxc_ip_cidr`, `vm_net0`) — plain snake_case, no leading underscore (meta 003's `_lxc_module_args`
  sketch was illustrative, not a naming mandate).

- **(C) Allowlist contents → the mandatory minimum set PLUS the dossier's full extended candidate
  list, kept as-is.** Minimum is mandatory (the keys the repo routes today + the two fixes). The
  extension is kept in full rather than trimmed because the allowlist is a *filter*: an extra key
  that is a valid module param merely lets a user override it without another backlog item, and one
  that is **not** valid is caught and dropped by the mandatory `ansible-doc` cross-check
  (Verification step 2) before landing — so a generous list carries no runtime risk, only a
  bounded, mechanical trim. Deliberately excluded from both lists (the whole point of the fix):
  `network`, `ip_address`, `ansible_host`. Exact lists are in the Plan's Edit 1 / Edit 2 blocks.

- **(D) Dropped keys → silent, no warn/debug task.** Meta allows either. Silent is chosen because,
  under the filter approach, `homelabinfra_instance.lxc`/`.vm` **always** carries at least
  `ip_address` (playbook-injected) and usually `network` (user default) — both intentional drops.
  A naive "dropped keys" warning would therefore fire on essentially every run listing keys the
  user did nothing wrong with, which is noise, not help; making it meaningful would require a second
  exclusion list (`network`/`ip_address`/`ansible_host`) — machinery beyond this narrow fix. So no
  warn task; the diff stays to one inserted task per file.

- **(E) `api_user` routing → injected into the projected local via `combine`, NOT added to the
  instance dict.** `api_user` lives at `homelabinfra_config.proxmox.api_user` (one level above the
  `.lxc`/`.vm` subtree) and is not merged into `homelabinfra_instance.*` today, so `selectattr`
  cannot pick it up. Two ways to route it: (a) add it to the `Build facts` explicit dict, or (b)
  combine it onto the filtered args. Chose **(b)** because acceptance requires
  "`homelabinfra_instance.lxc` / `.vm` content is unchanged" — option (a) would change the instance
  dict (and, via the JSON dump, add `api_user` to the in-guest file, brushing against the
  strip-secrets item's turf). Option (b) leaves the instance dict byte-identical and puts `api_user`
  only where it is needed. `api_user` is still listed in both allowlists (per acceptance's
  enumerated lists and for documentation that it is a routed module param); the `combine` is what
  actually supplies it, since it is absent from the instance dict. It has a git-managed default
  (`root@pam`), so it is always defined — no new assert (matches Context defect 1).

- **(F) `features` default shape → block-list `- nesting=1` (== `["nesting=1"]`).** The module's
  `features` is `list[str]` of pct option strings; the current `{nesting: true}` dict fails the
  list type-check. Block-list form is used over the flow form `["nesting=1"]` only for consistency
  with the file's adjacent `tags:` block style; the two are identical YAML. `nesting=1` is the pct
  feature string equivalent of the old `nesting: true` intent. No other default value changes.

<!-- No NEEDS HUMAN items: every decision was resolvable from the dossier plus the three named
     files plus meta 003's README. -->


## Verification

### 1. Static gates (the only real gates — `.claude/build.yml`)

Run both, capturing the real exit code **inside** the WSL call (a Bash-tool "exit 1" on these has
twice been a WSL relay artifact — see Context):

- `lint`: `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/lint.sh; echo RC=$?'`
- `test`: `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/test.sh; echo RC=$?'`

**What the gates prove:** `lint` runs ansible-lint over `playbooks/`/`roles/`/`tasks/`/`vars/`, so
it *does* parse all three edited files — it proves the new `set_fact` tasks, the folded-scalar Jinja,
the `vars:` allowlist blocks, and the `features: [- nesting=1]` default are lint-clean YAML with no
**new** diagnostics vs base. `test` runs `--syntax-check` over playbooks (which import these task
files). **Neither gate executes the Proxmox module call**, so neither can prove the filtering
behavior itself — that is proven by inspection (steps 2-3). Known pre-existing `test`-gate `[ERROR]`
diagnostics (docker role missing; `instance` undefined in `restart-app`/`tail-applog`; empty
`rollback-container` playbook) are accepted **only if identical on base** — a diagnostic that is new
or gone is a regression to investigate (stash the diff, re-run `test.sh` on the clean tree, compare),
per `.claude/plans/done/reconcile-config-example.md` Run log.

### 2. Allowlist evidence — cross-check every key against the community.proxmox 2.0.0 spec

The gate venv pins `community.proxmox==2.0.0`, so its argument specs are authority. Dump both
schemas (these are **not** gate commands and not on any `allow:` list — each prompts the human once;
expected and fine in an interactive run):

```
wsl bash -lc '~/.venvs/homelab-ansible/bin/ansible-doc -j community.proxmox.proxmox'
wsl bash -lc '~/.venvs/homelab-ansible/bin/ansible-doc -j community.proxmox.proxmox_kvm'
```

The valid parameter names are the keys of `.["community.proxmox.proxmox"].doc.options` (resp.
`proxmox_kvm`). Writing the JSON to a scratch file and extracting the option keys (e.g. a small
`python3 -c` snippet in the scratchpad — avoids shell-relay quoting hazards) is the reliable path.
Then, for **every** key in `lxc_module_keys` and `vm_module_keys`:

- Confirm it appears as an option in the corresponding dump. **Any key absent from the dump is
  removed from the allowlist** (not worked around) and the removal recorded in the Run log with the
  key name. This is a mechanical presence check, not a judgment call.
- The mandatory minimum keys (Context lines 120-126) are expected present; if a **minimum** key is
  absent from the 2.0.0 dump, that is a surface-worthy finding (report it, do not silently drop) —
  none expected. Extended keys that are absent are trimmed silently and listed in the Run log.

Record in the Run log: the final landed allowlist for each module, and any candidate keys trimmed.

### 3. Inspection (proves what the gates cannot — check against the diff)

1. **LXC module args are filtered.** `lxc-create.yml`'s `community.proxmox.proxmox` task now takes
   `{{ lxc_module_args }}`; the raw `homelabinfra_instance.lxc` is no longer splatted into the
   module; `lxc_module_keys` is a literal list in the file.
2. **VM module args are filtered.** Same for `vm-create.yml` / `community.proxmox.proxmox_kvm` /
   `vm_module_args` / `vm_module_keys`.
3. **Bookkeeping keys cannot reach either module.** `network`, `ip_address`, `ansible_host` are
   absent from both allowlists; the projection admits allowlisted keys only, so a user's
   `proxmox.lxc.notes_for_humans: "foo"` (or the real `network`/`ip_address`/`ansible_host`) is
   dropped.
4. **Every routed key is present.** LXC list contains `netif`, `nameserver`, `searchdomain`,
   `pubkey`, `vmid`, `hostname`, `ostemplate`, `cores`, `memory`, `tags`, `features`, `password`,
   `description`, `onboot`, `disk_volume`, `storage`, plus
   `state`/`api_host`/`api_port`/`api_user`/`api_token_id`/`api_token_secret`/`node`. VM list
   contains `net`, `ipconfig`, `nameservers`, `searchdomains`, `sshkeys`, `ciuser`, `vmid`, `name`,
   `agent`, `onboot`, `autostart`, `description`, `tags`, plus the same api/connection keys.
5. **`api_user` routed.** Both projections `combine({'api_user': homelabinfra_config.proxmox.api_user})`,
   and `api_user` is not added to the `Build facts` set_fact (instance dict unchanged).
6. **`features` default.** `homelabinfra-defaults.yml` ships `lxc.features` as `- nesting=1`
   (list[str]); no other default value changed; `#TODO` comment intact.
7. **Instance dict + downstream untouched.** In both files the diff shows exactly: one inserted
   `set_fact` task and one changed args source on the module task — nothing else. The `Build facts`
   set_fact, both network set_facts, the vmid waits, and the in-guest JSON dumps are byte-identical;
   `homelabinfra_instance.lxc`/`.vm` content (hence the JSON dump content and every
   `add_host`/`create-docker-host`/`find-or-create-host` consumer) is unchanged — the JSON dump does
   **not** gain `api_user`.
8. **Diff scope.** `git diff --name-only` shows exactly `ansible/tasks/proxmox/lxc-create.yml`,
   `ansible/tasks/proxmox/vm-create.yml`, `ansible/vars/homelabinfra-defaults.yml` (plus this plan
   file). No other task, playbook, role, or loader file appears.

### 4. korr-qa senior pass confirms before commit

Both gates green with pre-existing diagnostics identical to base; every allowlist key present in the
2.0.0 `ansible-doc` dumps (trims recorded); the eight inspection points hold on the diff; the
instance dicts are unchanged; diff scope is exactly the three files.

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
