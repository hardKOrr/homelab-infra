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
