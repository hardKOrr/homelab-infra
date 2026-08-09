# 404 — Uptime Kuma role + playbook

**Status:** built
**Subject:** Uptime Kuma
**Related:** 303 (monitor registration over socket.io), 401 (Ntfy)

## Goal

Deploy Uptime Kuma as Docker-on-LXC — bootstrap step 5 — and leave it **usable**, not
merely running: database chosen, admin account created, API key minted, Ntfy notification
channel configured, all without a human.

Reopened and largely closed on 2026-08-08. The app had never initialized: Kuma 2 asks for
a database backend before it will do anything, the role did not answer, and it sat on its
setup screen for five days across four green bootstrap runs. Two facts turned out to be
wrong and both are now settled — the database step is plain HTTP
(`POST /setup-database {"dbConfig":{"type":"sqlite"}}`, and the payload shape matters), and
an API key does **not** require a browser session: Kuma issues keys to an authenticated
*socket*, so `login` then `addAPIKey` over long polling mints one.

Monitor registration is not in this slice — it is socket.io-only in every Kuma version and
belongs to **303**. What this slice proved is the dependency 303 needed: socket.io is
drivable from Ansible with four `uri` tasks and no Python client.

## Remaining

- [ ] Ntfy notification channel configured and visible — reachable now that the app is
      initialized, and proved on a throwaway instance by 303, but unobserved on the lab's
      own instance
- [x] Kuma UI loads, admin user created without human intervention — execution 36. The
      instance now reports `42["loginRequired"]` on connect where it reported `42["setup"]`,
      and its database went from absent to 286 KB
- [x] An API key exists without human intervention — execution 37. The old manual
      three-step instruction is now a fallback that prints only if minting fails
- [x] The monitoring endpoint and key are recorded — in the `monitoring` registry key plus
      the vault item `homelab-infra/monitoring`. The original criterion named `api_url`,
      `api_token` and `ntfy_notification_id` in `facts.yml`; slices 200 and 014 moved that
      shape, so this is recorded met against the shipped one
- [x] Re-run is idempotent — execution 38, `changed=0`, reusing the recorded key. Unlike
      executions 31 and 32, which also said `changed=0` against an app that had never
      started, this converges on a *working* instance

## Links

- `ansible/roles/uptime-kuma/` — role, compose template, handlers, defaults
- `ansible/playbooks/apps/uptime-kuma.yml` (PATH A)
- `ansible/vars/app-defaults/uptime-kuma.yml`
- `config.example/apps/uptime-kuma.example.yml`
- `ansible/tasks/kuma/` — the shared socket.io conversation, owned by 303
- [notes.md](notes.md) — session narrative, and the superseded v1/v2 planning text
- [../LESSONS.md](../LESSONS.md) — "A deploy must assert usability, not liveness" is this
  slice's lesson; `GET /api/entry-page` is the check only an initialized Kuma passes
