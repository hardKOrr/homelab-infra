# Meta Index

The work queue. This file stays a table — prose belongs in [LESSONS.md](LESSONS.md),
per-session narrative in a slice's own `notes.md`, slice shape in [README.md](README.md).

**23 live · 23 archived in [done/](done/) · 3 unreachable in [no-target/](no-target/).**

## How this queue is organized

Every live slice is code-complete and gate-green. **There is no implementation work left in
this backlog.** What remains is roughly ninety criteria that all say the same thing: watch it
happen on the real lab.

So the queue is cut by **sitting** — one block of hands-on lab time — not by code role. A
slice appears under every sitting that closes part of it, and closes when its own
**Remaining** section is fully ticked. Working a *slice* is how the last two weeks produced
hardening sidequests instead of closures; work a **sitting**, and close whatever it ticks.

Gates (both green, both under WSL): `wsl bash -lc 'bash .claude/gate/lint.sh'` and
`.claude/gate/test.sh`.

## The sittings, in order

| # | Sitting | Slices it ticks | Blocked on |
|---|---|---|---|
| **1** | **Unblock secret writes.** One task's `no_log` off, one Deploy job, read `bw` stderr. | 016, 014 | nothing — do this first |
| **2** | **The deploy sweep.** With writes unblocked, run every per-app Deploy job once from Rundeck, then the day-2 jobs against what they produced. | 011, 201, 300, 302, 400, 501, 502, 504, 505, 601 | sitting 1 |
| **3** | **The browser sitting.** One session at a browser: Vaultwarden vault CRUD, Grafana admin login, Authentik admin login, one app through `forward_auth` sign-in. | 302, 306, 400, 403, 405 | sitting 2 |
| **4** | **The second estate.** Declare a second domain, deploy one app into it, watch DNS-01 issue its own cert. | 008, 009, 015, 407 | sitting 3 |
| **5** | **The router.** OPNsense host overrides. | 304 | OPNsense API credentials |
| **6** | **Bootstrap from bare metal.** Wipe the runner, re-run the bootstrap script, come back up from Key Storage alone. | 010, 012, 013, 014 | do last — it destroys the lab that sittings 2–4 run on |

### Sitting 1 is the only thing that matters right now

Everything in sitting 2 stores a secret, and secret storage is broken. The automation
account's collection-grant write failed live 2026-08-09 (`bw edit org-collection`,
execution 48) *after* the read, the org resolution and the member lookup all succeeded. The
error is masked by `no_log`. Unmask that one task, run any Deploy job, read the stderr.

Two outcomes, and the stderr distinguishes them: either Manager can rewrite a collection's
grants and something else is wrong with the call, or it cannot and the owner grants the
collection once at enrollment. **Do not reason about which is more likely — the run answers
it in one line.** That reasoning is exactly the loop this queue exists to end.

## Deferred — needs a drill, not a deploy

Real criteria on shipped code, but each needs a rehearsed failure rather than a normal run.
They are out of the critical path deliberately. Nothing depends on them.

| Slice | The criterion | Why it is deferred |
|---|---|---|
| 010 | Restoring the runner LXC from PBS yields a working runner with `config/` intact | a full DR drill |
| 014 | Interrupting bootstrap before cutover is resumable | fault injection; already attempted and refused twice, 2026-08-06 |
| 015 | Internal-CA mode; the no-API-provider handoff | the lab has a real domain on an API-backed provider, so neither path runs here |
| — | PBS token rotation (see caveats) | needs the token deleted PBS-side to force the create path |

## By subject

Slices are cut on the code axis, so one subject spans several. Look up the subject, then
read only those slices.

| Subject | Slices |
|---|---|
| Vaultwarden | app **400**, secret store **014**, token capture **013**, identities **016** |
| Caddy / TLS | wiring **300**, DNS-01 **407**, wildcard bootstrap **015** |
| Authentik / identity | app **403**, wiring **302**, forward_auth **306**, modes **009** |
| Config model | provenance **010**, onboarding **012**, estates **008** |
| Networking | IP allocation **011**, OPNsense **304** |
| Media | apps **505**, wiring **504** |
| Runners / UI | Rundeck **601** |
| Day-2 ops | watchtower **201**, remove **501**, rollback **502** |

## Carried caveats

- **PBS's new token check ran against an inherited token, not a created one.** Exec 44
  (2026-08-09) verified the effective token after the ACL grant and passed, but the recorded
  token still authenticated, so `Remove the unusable API token`, `Create the API token` and
  `Grant the token Admin on the datastore tree` all skipped. Deleting the token PBS-side
  forces the path and closes it.
- **500's one staged import is `apps/nginx.yml`**, which does not exist — 301 shipped the
  wiring pair only, no app playbook.
- **010/012's bootstrap script is the proven path, not an experiment.** It has run from a
  full wipe — old runner and predecessor destroyed, then exit 0 on three consecutive runs,
  2026-08-01 — and the current lab was built by it. What sitting 6 adds is narrow:
  `config-doctor` on the live runner, Lab Status green with the token supplied only from
  Key Storage, and the runner's vmid inside the PBS backup job.
