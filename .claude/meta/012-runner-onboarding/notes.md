# 012 — notes

Append-only.

## 2026-08-01 — first from-scratch run: SIGPIPE killed the script at the password step

The first live execution of `bootstrap-rundeck.sh` against a bare node died silently at
"replacing default admin password". Everything after it — ansible venv, repo clone, the
platform SSH identity, `lab-run` wiring, collections, config authoring, the Proxmox
credential, project creation, Key Storage, job import and the Vaultwarden deploy — never ran.
The only symptom in the log was one line:

```
tr: write error: Broken pipe
```

**Cause.** `bootstrap-rundeck.sh:388`, inside the guest heredoc that runs under
`set -euo pipefail` (line 290):

```sh
RD_ADMIN_PW="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 28)"
```

`/dev/urandom` is an infinite producer. `head -c 28` reads its 28 bytes and exits, `tr`'s
next write takes `EPIPE`/`SIGPIPE`, and `pipefail` hands the resulting **141** to `set -e`.
Reproduced directly on the node:

```
$ bash -c "set -euo pipefail; v=\$(tr -dc A-Za-z0-9 </dev/urandom | head -c 28); echo GOT=\$v"
exit=141          # and GOT= never printed
```

**Fix.** Bound every producer and let nothing exit early, then slice with parameter
expansion rather than a second reader:

```sh
RD_ADMIN_PW_POOL="$(head -c 96 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
RD_ADMIN_PW="${RD_ADMIN_PW_POOL:0:28}"
[ "${#RD_ADMIN_PW}" -eq 28 ] || { echo "could not generate an admin password" >&2; exit 1; }
```

Verified exit 0 with a 28-character password. `grep -rn 'head -c'` over the repo confirms
this was the only occurrence of the pattern — the fix is contained to this one site.

**Why it was never caught.** This is precisely the class of defect the `built` status exists
to flag. Both gates are green and stay green: `lint.sh` is ansible-lint and `test.sh` is
`ansible-playbook --syntax-check`; **neither gate looks at shell scripts at all**, and
`bootstrap-rundeck.sh` is the largest piece of shell in the project and the entry point to
everything else. Worth considering `bash -n` (and shellcheck, if it earns its keep) over
`rundeck/*.sh` and `ansible/scripts/*.sh` in `lint.sh`.

**Why it survived the previous runner.** LXC 13228 was built by an earlier revision of the
script, and its `realm.properties` had already been customised — so on any re-run the
`grep -q '^admin:admin,'` guard was false and line 388 never executed. The bug was only ever
reachable on a genuinely fresh container, which is exactly the path the acceptance criterion
names ("on a bare Proxmox node") and the one that had never been run.

## 2026-08-01 — second failure: the privilege set was version-locked to PVE 8

With the SIGPIPE fix in, the run reached `Proxmox credential` and died there:

```
==> Proxmox credential
    creating role HomelabInfra
400 Parameter verification failed.
privs: invalid format - invalid privilege 'VM.Monitor'
ERROR: could not create role HomelabInfra
```

**Cause.** The lab runs **PVE 9.2.4**, and `VM.Monitor` was removed in PVE 9 (superseded by
the `VM.GuestAgent.*` family). `pveum role add` rejects the *entire* list on the first
unknown name, so one stale privilege fails the whole credential step — and with it the
project, Key Storage, job import and Vaultwarden.

The script already knew this class of drift existed: it carried a bespoke `PVE_PRIVS_NOSDN`
fallback because `SDN.*` did not exist before PVE 8. That approach needs a new branch for
every future vocabulary change and had not been extended for the PVE 9 removals.

**Fix.** Discover the vocabulary instead of hardcoding it. The `Administrator` role holds
every privilege a node knows, so it *is* the vocabulary; intersect the wanted set against it
and report anything dropped:

```sh
PVE_PRIVS_SUPPORTED="$(pvesh get /access/roles/Administrator --output-format json 2>/dev/null \
  | tr ',' '\n' | tr -d '{}" ' | sed 's/:1$//' | grep -E '^[A-Za-z]+\.[A-Za-z.]+$' || true)"
```

`VM.Monitor` was also removed from the wanted set outright — nothing in the repo uses QEMU
monitor access — so the intersection now exists purely as drift protection, and the
`PVE_PRIVS_NOSDN` fallback is deleted as redundant.

Verified live before re-running: vocabulary of 47 privileges discovered, exactly `VM.Monitor`
dropped from the old list, 27 kept, and `pveum role add` accepted the result (test role
created and deleted).

**Generalisation worth keeping.** Both failures so far are the same shape: the script asserts
a fact about its environment that was true where it was written and is not true everywhere it
is meant to run. This one matters more for the shareability claim than for this lab — anyone
on PVE 9 hits it on their first run, which is every new adopter from here on.

## 2026-08-01 — the from-scratch run, completed: 15 blockers

The old runner (LXC 13228, built 2026-07-26, five slices out of date) and its stale
predecessor 13227 were destroyed. `bootstrap-rundeck.sh` was then run from nothing, thirteen
times, against `master`. **The first acceptance criterion is now observed.**

Runner: LXC **168000002** at 192.168.0.2/20, tagged `homelab-infra`, VMID derived from the
address by the repo's own scheme. Guests land at 192.168.0.10+.

### Verified end state

| Check | Result |
|---|---|
| `bootstrap-rundeck.sh` exit | 0, on three consecutive runs |
| Rundeck project + jobs | created, **19/19 imported**, no UI steps |
| Key Storage | `keys/proxmox/api-token` + `keys/rundeck/homelab-ssh` staged |
| Proxmox credential | `homelab-infra@pve` role `HomelabInfra`, minted and rotated cleanly |
| `config/` authored | `proxmox.yml`, `infrastructure.yml`, `apps/rundeck.yml` |
| `.generated/facts.yml` | `runner` + `vaultwarden` keys, correct path |
| Vaultwarden | 1.37.1 active+enabled, **HTTP 200** at 192.168.0.10, lab-* scripts installed |
| Admin token sink (013) | `VAULTWARDEN_ADMIN_TOKEN` at 0600 — **one pass, no paste** |
| Idempotency | full re-run reused runner and guest, created nothing duplicate |

### The fifteen

Four in shipped Ansible; eleven in the seam between the repo and the machine that runs it.

| # | Where | Defect |
|---|---|---|
| 1 | `bootstrap-rundeck.sh` | `tr </dev/urandom \| head -c` under `pipefail` → SIGPIPE 141 |
| 2 | `bootstrap-rundeck.sh` | `VM.Monitor` removed in PVE 9; privilege list version-locked to PVE 8 |
| 3 | `bootstrap-rundeck.sh` | `apiCookieAccess` off — the first API token cannot be minted |
| 4 | `ansible/scripts/lab-run.sh` | mode 100644 in git; **all 19 jobs** would fail `exec lab-run` |
| 5 | `bootstrap-rundeck.sh` | Rundeck rotates JSESSIONID; `-b` without `-c` sent a stale session |
| 6 | `bootstrap-rundeck.sh` | `netaddr` absent from the runner venv (the gate venv had it since 2026-07-06) |
| 7 | `lxc-create.yml`, `vm-create.yml` | `set_fact` sibling key reference — undefined by construction |
| 8 | both create tasks | `validate_certs` missing from the module allowlists; PVE is self-signed |
| 9 | 7 files in `vars/` | storage pinned per-app; `local` cannot hold a container rootfs at all |
| 10 | 4 task files | `delegate_to: <node name>` — nothing ever made node names resolvable |
| 11 | `inventory/proxmox.yml` | `key: tags` / `type ==` matched nothing: **no `tag_*` group ever existed** |
| 12 | `lxc-create.yml` | `state: present` creates a stopped container the next task waits on |
| 13 | `inventory/proxmox.yml` | no `ansible_host` for guests — a reused guest was unreachable |
| 14 | 4 roles + template | `app_config.update` resolves to `dict.update`, never the config key |
| 15 | `write-generated-facts.yml` | app playbooks wrote `facts.yml` two levels up, into a path nothing reads |

**#11 and #15 are the two that mattered most.** #11 meant idempotency never worked — a re-run
could not find the guest it had made and would provision a second one — and `proxmox_clients`
being absent meant IP allocation never excluded an address already in use. #15 meant no app
could ever record an endpoint another app could read, so the bootstrap ordering the whole
baseline sequence rests on did not hold.

**Neither gate saw any of the fifteen.** `lint.sh` and `test.sh` stayed green throughout —
they check Ansible syntax and style, and none of these were syntax or style. #6 is the
sharpest illustration: `.claude/gate/requirements-dev.txt` pinned `netaddr` in July precisely
as the ipaddr filters' runtime dependency, so the gate environment had it and the runner
environment never did. **Green gates were never evidence the platform could run.** A smoke
target that provisions one throwaway guest would have caught most of this; `shellcheck` and a
file-mode assertion would have caught #1, #4 and probably #3.

**Nothing here was reachable before.** The lab's 57 guests are all hand-built and untagged, so
no guest this repo created had ever existed. Every one of these defects sat behind that fact.

## 2026-08-01 — the Bootstrap Platform click, completed: 25 more blockers

The seven baseline services had never run. This session deployed them, then ran
`Bootstrap Platform` through the Rundeck API until it succeeded on three consecutive
clicks. **The second acceptance criterion is now observed.**

Twenty-five blockers, numbered 16–40 continuing the fifteen above. Ten executions of the
job; the first six each failed at a different point and moved the frontier forward.

### Verified end state

| Check | Result |
|---|---|
| `Bootstrap Platform` | **succeeded on executions 7, 8 and 9** — API-triggered, no CLI |
| Vaultwarden | 200 at 192.168.0.10 |
| Ntfy | health 200; anonymous publish **403**, authenticated publish **200** |
| Caddy | admin API 200 at 192.168.0.12:2019, six routes, correct upstreams |
| Authentik | 4 containers healthy on **sso-stack** .16, API token authenticates |
| Uptime Kuma | serving on monitoring-stack .14 |
| Prometheus + Grafana | Grafana 13.1.1, **7/7 scrape targets up** |
| PBS | services + datastore `homelab` + PVE storage `pbs-homelab` + backup job |
| Registry keys | backups, domain, metrics, monitoring, notifications, reverse_proxy, runner, sso, vaultwarden |
| Convergence | **five consecutive green clicks (7–11)**; by 11, caddy, monitoring-stack, sso-stack and vaultwarden all at **changed=0** |

### The twenty-five

| # | Where | Defect |
|---|---|---|
| 16 | `lxc-create.yml` | YAML `#` comment inside a Jinja `{{ }}` — every LXC create died templating |
| 17 | `lxc-create.yml`, `vm-create.yml` | `state: started` never CREATES; it resolves a guest first and raises |
| 18 | `ensure-cloud-template.yml` | vmid 9000 treated as "our template" on existence alone; adopts a hand-built guest |
| 19 | all three provisioning tasks | `/cluster/resources` is eventually consistent; module reads `name` unconditionally |
| 20 | 4 app playbooks + `find-or-create-host.yml` | guest reuse never STARTED a stopped guest |
| 21 | `roles/caddy` | `--resume` makes Caddy ignore `--config`; base config never applied |
| 22 | `homelabinfra-defaults.yml` | no `ostemplate` — **no Docker app could ever deploy** |
| 23 | `lxc-create.yml` | PVE forbids non-`nesting` features to a non-root@pam token |
| 24 | `resolve-estate.yml` | `domain` had one writer (`bootstrap.yml`); standalone deploys failed |
| 25 | `wiring/uptime-kuma.yml`, `roles/uptime-kuma` | a 200 of `text/html` read as a working REST API |
| 26 | `roles/observability` | node-exporter container vs the package on every guest — port 9100 collision |
| 27 | `bootstrap-rundeck.sh` | runner had no node_exporter; permanently DOWN scrape target |
| 28 | `vm-clone.yml` | `validate_certs` missing — the one file 012's blocker 8 could not reach |
| 29 | `vm-clone.yml` | waited for a guest agent absent from the cloud image |
| 30 | `roles/pbs` | the PBS package adds an enterprise apt repo that 401s without a subscription |
| 31 | `roles/pbs` | `generate-token` rejects `--output-format` and prefixes its JSON with `Result: ` |
| 32 | `configure-pbs.yml` | datastore POST returns a UPID; PVE was told to use it before it existed |
| 33 | `configure-pbs.yml` | backup job included the cloud template (self-inflicted by 18's tag) |
| 34 | `.gitignore` | bare `config/` excluded **`ansible/tasks/config/`** from the repo |
| 35 | `load-user-vars.yml` | relative paths encoded the CALLER's depth; bootstrap.yml never ran |
| 36 | `roles/ntfy` | user-existence regex never matched — re-running Ntfy always failed |
| 37 | `lxc-create.yml` | `pct exec` ready is not sshd listening |
| 38 | `wiring/authentik.yml` | application list filtered by object permission; could not see what it created |
| 39 | 4 app playbooks | Docker install gated on "did I create this host THIS run" |
| 40 | `roles/observability` | unsorted scrape targets restarted Prometheus every pass |

### What this run was actually testing

Almost nothing in this platform was idempotent, and that is the session's finding.

Blockers 32, 36, 37, 38, 39 and 40 are one defect in six costumes: an "is it already there?"
check that answered wrong, so the code re-created something that existed and failed — or
rewrote something unchanged and restarted a service. They were invisible to the per-app loop
because **that loop only ever ran each service once successfully**. The moment a service
worked, it was done.

`Bootstrap Platform` is the first thing in this project's history that runs all seven
services in sequence against a lab that already has them. It is not a harder test of the
services; it is the only test of convergence, which is the property CLAUDE.md leans on
hardest — *"re-running a deploy IS the update mechanism"*.

A related family — 19, 21, 32, 37 — is async-vs-sync: something returns success before the
work it started has finished. Three of those four only appear when a resource is created and
consumed in the same run, which is exactly what a one-click bootstrap does.

And four more — 18, 21, 26, 30 — were **confident comments asserting something untrue**.
"vmid 9000 is our template", "`--config` is the first-boot seed", "every OTHER guest runs
node_exporter as a package", "the no-subscription repository is used deliberately". In each
case the code did exactly what the comment said and the comment was wrong about the world.
These are the ones that stay hidden longest, because the code reads as though someone checked.

### Status corrections

Three entries in INDEX.md are less true than recorded. None of these are new work — they are
claims the live run disproved.

- **010 — the Config job group has never worked on a runner.** `tasks/config/run-doctor.yml`
  and `write-config-file.yml` were never in git (blocker 34), so `Config Doctor` and
  `Configure App` would fail identically on any machine that cloned the repo. INDEX already
  said "verified on the workstation; nothing the script does is" — the workstation was the
  only place they could have worked. The `Configure App` / `Get Config` transport story that
  replaced the rejected lab-repo idea is still unexercised end to end.
- **302 — the Authentik wiring was verified for first-time creation only.** Blocker 38 means
  every app's SECOND deploy failed at SSO wiring. "wire/unwire verified live" has meant
  "wire once".
- **404 — the premise is false.** Uptime Kuma 2.5.0's entire HTTP surface is 16 routes and
  every one is a GET. There is no REST write API in any version; monitors, notifications and
  first-run setup are all socket.io, and the API key gates the Prometheus `/metrics`
  endpoint rather than granting CRUD. v2 was locked in to get an API that does not exist, so
  monitor auto-registration cannot work as designed. The operator has accepted socket.io
  (`lucasheld/uptime-kuma-api`) as later work — its version matrix tracks 1.x and 2.x changed
  the setup flow, so support needs verifying before committing to it.

### Known properties, not bugs

- **Relocating the SSO provider takes two bootstrap passes to converge.** Authentik deploys
  at step 4, but Vaultwarden (1) and Ntfy (2) wire their SSO before it. On the pass where
  Authentik moves stack, those two wire against the old address and the next pass corrects
  them. It self-heals and nothing breaks. Inherent to the ordering: the SSO provider cannot
  be both before and after the apps that wire into it.
- **Residual `changed` on a converged run is 10, all accounted for**: `add_host` (7, always
  changed by design), two `changed_when: true` ACL grants that are declarative
  re-applications, and Ntfy's deliberate authenticated-publish proof. Four of six hosts
  report `changed=0`.
- **A fix that changes rendered output costs one extra pass.** Blocker 40's sort landed in
  execution 10, which still reported the Prometheus config changed — that run rewrote the
  file from the old arbitrary order into the sorted one. Execution 11 was `changed=0`. Worth
  remembering when reading a single run's counts: the pass that lands a fix is not the pass
  that demonstrates it.

### Stack layout, revised during the run

The operator moved Authentik onto its own stack. Its own app-defaults had argued for that
already — "it carries a database, and a shared host would couple every co-tenant's restarts
to the platform's login path" — while the value said `core_stack`, so the comment and the
assignment disagreed. Uptime Kuma joined observability on `monitoring_stack`; `core_stack` is
retired. Sizing moved from per-app to `vars/stack-defaults.yml` keyed by stack, because a
stack host is shared and the first app to deploy creates it — per-app sizing made the host's
size depend on deploy order.

LXC **168000013** (`core-stack`) is left over from the old layout and holds nothing the
platform references. Destroying it is the operator's call and was not done.

### Gates

`.claude/gate/jinja-parse.py` was added to `lint.sh`: it walks every string scalar under
`ansible/` and compiles the ones containing `{{` or `{%`. It catches blocker 16's class,
which both existing gates are structurally blind to — they parse YAML and never compile a
template string.

It catches nothing else here, and that is the point. **All twenty-five were found by running
the thing.** Of them, 34 and 35 could not have been found any other way: 34 needs a fresh
clone (the working tree is correct), and 35 needs a playbook at a depth no development run
uses. The smoke target INDEX already suggests — provision one throwaway guest and immediately
use it — would have caught 19, 32, 37 and probably 20 in a single pass.
