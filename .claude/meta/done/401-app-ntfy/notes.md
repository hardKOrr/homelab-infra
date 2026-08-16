# 401 — notes

## 2026-07-25 — implementation

Implementation complete and gate-verified; slice stays in-progress until a live
deploy confirms the acceptance items.

### What's in

- `roles/ntfy/` — install from the official `_linux_amd64.deb` asset on the latest
  GitHub release, templated `/etc/ntfy/server.yml`, publish account + access token
  provisioning, three `lab-*` scripts, `facts.d` version record.
- `playbooks/apps/ntfy.yml` — PATH B native LXC, mirroring `apps/vaultwarden.yml`
  (tag-based find-or-create so re-runs do not provision a duplicate guest).
- `vars/app-defaults/ntfy.yml`, `config.example/apps/ntfy.example.yml`.
- `tasks/notify.yml` — shared one-shot publisher, see below.

### Decision: the server ships closed, and that changed five consumers

The README called for `auth-default-access: deny-all`, and acceptance item 2 is
"unauthenticated POST is denied". Every notification consumer already in the repo
posted anonymously, so honouring that meant reconciling all of them:

- `tasks/bootstrap/configure-unattended-upgrades.yml`
- `tasks/bootstrap/configure-watchtower.yml`
- `tasks/bootstrap/configure-pbs.yml` (webhook target + test message)
- `playbooks/maintenance/check-native-updates.yml`
- `playbooks/maintenance/restart-app.yml`

Contract change (CONTRACT.md §3): `notifications` gains provider-specific optional
fields `user`, `password`, `token`. Consumers treat them as **optional** — an
absent token means an Ntfy predating this slice, and they fall back to an
unauthenticated POST rather than failing. That keeps the change from breaking an
existing lab on `git pull`.

`uri`/`curl` consumers send `Authorization: Bearer <token>`. Watchtower is the
exception: shoutrrr authenticates to Ntfy with the basic-auth pair, so its URL is
built as `ntfy://user:password@host/topic`, which is why both the password and the
token are recorded.

### Deviation: credentials are not written to Vaultwarden

The README said "persist credentials to Vaultwarden via `community.general.bitwarden`
lookup write". That lookup is **read-only** — there is no write path in the
collection, and `tasks/bitwarden/todo/` is still a stub. Credentials therefore live
in gitignored `config/.generated/facts.yml` (0600) on the controller, which is where
every other generated platform secret already lives. Storing them in Vaultwarden
needs a real client (`bw` CLI or the Secrets Manager API) and belongs in its own slice.

### Credential continuity

Generated once, then reused from `homelabinfra_infra.notifications.*` on every
re-run — otherwise each deploy would rotate the password and strand every notifier
already configured with it. Recovery is self-healing: a lost `facts.yml` regenerates
the password and resets it on the account, and the token is recovered from
`ntfy token list` by its `homelab-infra` label.

The guest never holds a plaintext credential from this role — ntfy keeps a bcrypt
hash in its own auth database.

### Guest-held publish credential (spec exception, deliberate)

`specs/secrets-handling.md` says no secret is ever written to a managed
guest's filesystem. A guest that reports its own OS updates or container updates
must be able to publish, so two files break that rule narrowly:

- `/etc/homelab-infra/ntfy.env` (0600 root, in a 0700 directory) — sourced by the
  unattended-upgrades notify script, which is 0755 and therefore must not carry the
  token itself.
- `/opt/watchtower/docker-compose.yml` (0640 root, 0750 directory) — the shoutrrr URL.

Both hold a capability scoped to publishing on one topic: no read access, no bearing
on any other service. Rationale is recorded in both task files. Rotate by clearing
`notifications.token` from `facts.yml` and re-running the Ntfy deploy.

### Shared notifier

`tasks/notify.yml` was added rather than open-coding the same `uri` block in seven
playbooks. It is a silent no-op when the provider is not ntfy or the registry has no
`notifications` key, and it is `failed_when: false` — a notification must never be
the thing that fails an otherwise successful deploy.

### Verification

- ansible-lint: clean (production profile passed while only `min` was required).
- syntax-check: `playbooks/apps/ntfy.yml` clean. Repo-wide, the only failure is the
  pre-existing empty `stacks/rollback-container.yml` stub (slice 502, untouched).
- NOT verified live.

### Live acceptance TODO

- `curl -u user:pass https://ntfy.<domain>/homelab -d hello` produces a notification.
- Unauthenticated POST returns 401/403 (the role asserts this on every deploy).
- `facts.yml` `notifications` block is populated with host, topic, user, password, token.
- Re-run: no duplicate users, no password rotation, no service bounce.
- Watchtower and unattended-upgrades notifications actually arrive on a guest.
- `ntfy token list` parsing holds on the then-current ntfy release (the recovery path
  depends on the CLI's output format).

## 2026-07-25 — post-review correction (shared with 400, 402–406)

A review recorded in `.claude/meta/500-bootstrap-plays/notes.md` caught a blocker in
the shape this slice established and the other five copied: Play 1/2/3 used a **shared**
`app_deploy` group. `add_host` groups persist for a whole run and `bootstrap.yml` chains
app playbooks with `import_playbook`, so under bootstrap the group accumulates — step 2
would have run the ntfy role on the Vaultwarden container and recorded
`notifications.host` from Vaultwarden's hostvars.

Fixed across all seven app playbooks and `_template.yml`: per-instance
`deploy_{{ instance }}`, with `find-or-create-host.yml` taking a `deploy_group` input
for the Docker path. `hosts:` additionally needs
`{{ instance | default('_instance_unset') }}` because it is templated at parse time
(same idiom as slice 102). Full detail in 500's notes.

Also fixed there: `configure-unattended-upgrades.yml` moved out from under the
`homelab_bootstrapped` marker into an unconditional Play 2 import, so the Vaultwarden
and Ntfy guests — which bootstrap before `notifications` exists — get the Ntfy update
hook on a later run instead of never.
