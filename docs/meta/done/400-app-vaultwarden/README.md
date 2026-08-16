# 400 — Vaultwarden role + playbook

**Status:** done
**Subject:** Vaultwarden
**Related:** 013 (admin token capture), 014 (secret store), 016 (identities), 501 (removal)

## Goal

Vaultwarden is the enforced first baseline app — it stores every platform secret. Native LXC
deployment with its own auth, no Authentik in front, following the three-play PATH B
pattern. Play 3 wires Caddy, Uptime Kuma and DNS.

The install mechanism deviates from the slice's original plan because upstream ships no
GitHub binary assets; see notes.md. The admin-token bootstrap is no longer a console print
and a human paste — slice 013 replaced it with a durable machine-readable sink, generated in
one pass.

Deployed live and convergent 2026-08-01: the fresh runner workflow created the tagged LXC at
192.168.0.10, installed Vaultwarden 1.37.1, returned HTTP 200, and installed the three
`lab-*` commands. By execution 11 the play reported `changed=0`.

Since `f9347b3` the deploy also proves the app is *usable*: the web vault is served, the API
layer returns JSON, and the running process's `environment.vault` matches the `DOMAIN` this
run templated — the check a restart-less deploy would otherwise hide. All three ran green on
execution 42, 2026-08-09.

## Remaining

**None — closed 2026-08-14.** The public hostname and vault CRUD were exercised from a
browser, then all three native maintenance commands ran live through Rundeck executions
138–140.

- [x] Fresh deploy creates the LXC, installs Vaultwarden, and the web vault loads at the
      wired domain — LXC, install, direct HTTP and the Caddy route were already observed;
      public-hostname login and create/read/update/delete were confirmed 2026-08-14
- [x] `lab-update-check` reports installed vs latest correctly — execution 138 succeeded;
      direct output reported installed `1.37.1`, latest `1.37.1`, update unavailable
- [x] `lab-restart-app` restarts the service — execution 139 reported `changed=1`; the
      service was `active` afterward
- [x] `lab-tail-applog` shows journalctl output — execution 140 showed the clean stop/start,
      new process, and successful requests after restart
- [x] Remove playbook stops and unwires cleanly — covered by 501
- [x] First deploy preserves the admin token exactly once without exposing it in the log —
      013 superseded the original console-print mechanism with a durable sink
- [x] Subsequent re-runs are idempotent

## Links

- `ansible/roles/vaultwarden/` — tasks, handlers, defaults, meta, env template, and the
  three `lab-*` scripts
- `ansible/playbooks/apps/vaultwarden.yml` (PATH B, native LXC)
- `ansible/vars/app-defaults/vaultwarden.yml`,
  `config.example/apps/vaultwarden.example.yml`
- `ansible/tasks/vaultwarden/token-sink.yml` — owned by 013
- [notes.md](notes.md) — the superseded binary-release install plan
