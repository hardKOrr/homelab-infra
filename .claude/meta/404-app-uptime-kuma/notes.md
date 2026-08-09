# 404 — notes

## 2026-07-25 — implementation

Implementation complete and gate-verified; slice stays in-progress until a live
deploy confirms the acceptance items. **The REST surface this slice targets is
unverified against a running Kuma — see "What live acceptance must confirm".**

### Decision: Uptime Kuma v2 (the README's open question, now closed)

The README left this explicitly open: "lock in Kuma v1 + python lib, OR Kuma v2 if
stable". **v2 it is**, confirmed with the user before implementation.

Reasoning: v1 exposes no REST CRUD API at all — only socket.io — so choosing it would
mean replacing `tasks/wiring/uptime-kuma.yml` and `tasks/unwiring/uptime-kuma.yml`
with socket.io implementations driven by `uptime-kuma-api`, adding a Python
dependency on the Ansible controller, and reopening slice 303. v2 keeps the REST
wiring slice 303 already shipped.

**Consequences for slice 303:** its implementation stands as written, and its open
question ("if 404 lands on v1, this file gets replaced") is resolved in the direction
that needs no rework. The registry key stays `monitoring` (Shape B), which this
slice writes. 303 can flip to done once a live Kuma confirms the endpoints.

The image is pinned to the `2` major tag, with `app.image_tag` to pin a patch
release. Watchtower is allowed to carry patch updates within that major — Kuma keeps
its state in one SQLite volume with no cross-container migration coupling.

### The one thing that is not one-click, and why

Uptime Kuma issues REST API keys only to an authenticated browser session. There is
no documented endpoint to mint the first one. So the honest state of this slice:

- First deploy: the stack comes up, the admin account is created via `/setup`, the
  role reports the exact three-step manual instruction, and records everything it
  does know.
- Until a key exists, `monitoring.token` is empty and `tasks/wiring/uptime-kuma.yml`
  probes, warns and skips — which is exactly the degradation slice 303 designed for.
  App deploys keep succeeding.
- The operator pastes the key into `config/apps/<instance>.yml` as `app.api_key` and
  re-runs. Everything from that point is scripted, including the Ntfy notification
  channel.

This is a real gap against `.claude/specs/one-click-idempotent.md`, recorded rather
than papered over. It degrades loudly instead of failing, and monitoring is an add-on
that must never be the thing that fails an app deploy (slice 303 acceptance item 5).

### Contract addition

`monitoring` gains provider-specific optional `admin_user` / `admin_password`
(CONTRACT.md §3) — the operator needs them to sign in and mint that first key.

`notification_id` is written **only when a channel was actually provisioned**:
`wiring/uptime-kuma.yml` gates on `is defined`, so writing an empty string would
attach an invalid channel to every monitor.

### What live acceptance must confirm

Every REST call in this role is written against the v2 surface that
`tasks/wiring/uptime-kuma.yml` already assumes, and **none of it has been exercised
against a running Kuma**. Treat these as unverified until a real deploy says
otherwise:

- `POST /setup` accepts `{username, password}` and refuses a second call (the
  idempotency signal this role relies on).
- `GET /api/monitors` with `Authorization: Bearer <key>` returns 200.
- `GET`/`POST /api/notifications` exist and the ntfy provider config keys are named
  as templated (`ntfyserverurl`, `ntfytopic`, `ntfyAuthenticationMethod`,
  `ntfyaccesstoken`), and that the create response carries the new channel's id.

If any of these differ, the fix is local to this role plus `wiring/uptime-kuma.yml`;
the structure (probe, degrade, warn) does not change.

### Verification

- ansible-lint: clean (production profile).
- syntax-check: `playbooks/apps/uptime-kuma.yml` and `playbooks/bootstrap.yml` clean.
  Repo-wide, only the pre-existing slice-502 stub fails.
- NOT verified live.

### Live acceptance TODO

- Kuma UI loads; admin account created without human intervention.
- Ntfy notification channel present and firing on a real DOWN event.
- `facts.yml` `monitoring` block has host, token, notification_id.
- Re-run is idempotent (no duplicate notification channel, no password rotation).
- Confirm or correct each endpoint listed above.

## 2026-08-02 — the 200 that is not an API

Measured on the live lab (`louislam/uptime-kuma:2` on `monitoring-stack`):

```
$ curl -o /dev/null -w '%{http_code} %{content_type}' -H Accept:application/json \
    http://localhost:3001/api/monitors
200 text/html; charset=utf-8
```

`/api/monitors` is not a route. Kuma serves the Vue SPA's `index.html` as the catch-all
for anything it does not recognise, with a 200. Every probe in this slice and in 303 that
concludes "the REST API is usable" from `status == 200` concludes it wrongly, against
every version, with or without a token.

The consequence in the unwire half is worse than a silent no-op. In
`tasks/unwiring/uptime-kuma.yml` the probe passes, `_kuma_probe.json` is absent so the
monitor selection defaults to `{}`, the delete is skipped, and
`Assert monitor removed` re-fetches the same HTML and passes **vacuously**. A live
teardown on 2026-08-02 removed six apps and reported every monitor cleanly deleted
without a monitor ever existing (slice 501 notes).

Whatever replaces the REST calls here — socket.io, as this slice already accepts — must
key its "is the API usable?" check on the content type or the shape of the body, never on
the status code alone. `facts.yml` `monitoring.token` was empty on the live lab for the
same underlying reason: nothing this role calls over HTTP can create an API key.

## 2026-08-08 — live: Kuma has been sitting on its setup screen since it was deployed

Uptime Kuma 2.5.0 has never initialized. Five days after a green deploy it was still
printing, every restart:

```
[SETUP-DATABASE] db-config.json is not found or invalid: ENOENT ... 'data/db-config.json'
[SETUP-DATABASE] Starting Setup Database
[SETUP-DATABASE] Waiting for user action...
```

Its data directory held no database at all — no admin user, no monitors, no API key,
nothing for the wiring to write to. Everything downstream follows from that, and each
symptom had been recorded separately as its own puzzle:

- `GET /api/monitors` returning **200 `text/html`** is not a "SPA catch-all quirk". In
  setup mode Kuma serves the setup page for every path, so the probe, the delete and
  the verify-assert all pass against a server that has no API.
- Lab Status's MONITORS section reporting "unavailable" was accurate the whole time.
- The role's own step already says so: "Uptime Kuma setup endpoint returned 404
  (NOT initialised — 2.x has no POST /setup)". That message has been in every run's
  output since 2026-08-03 and reads as a tolerated deviation rather than the app
  never having started.

**Kuma 2 adds a database-selection step before the admin-user step**, and the role only
implements the latter. The deploy is green because nothing in it asserts the app is
usable — the container is up and healthy, and that is all it checks.

### The initialization IS drivable over plain HTTP

The assumption that this needs socket.io is wrong, at least for the first step.
Verified live against 2.5.0:

```
POST /setup-database  {"dbConfig":{"type":"sqlite"}}   -> {"ok":true}
```

Kuma then restarts itself into the normal server and the admin-user step becomes
reachable. The payload shape matters: `{}` and `{"dbType":"sqlite"}` both return
`"Invalid dbConfig"`, so it must be `dbConfig.type`.

What the admin-user creation and API-key minting need is still unproved — those are
the next thing to establish, and the API key may genuinely require socket.io.

**Care taken while testing this**: running `sqlite3 /app/data/kuma.db` to inspect the
schema CREATED the file as a side effect, and Kuma then found a pre-existing empty
database and skipped its schema bootstrap, failing with `no such table: setting`. The
files were moved to `/opt/uptime-kuma/preinit-20260808-171202/` rather than deleted and
Kuma restarted to its exact prior state — still waiting for setup, nothing lost. Worth
remembering: on a database-backed app, a read-only-looking client can be a write.

---

## Superseded planning text (moved from README, 2026-08-08)

Kept for provenance during the meta restructure. This was the slice's original "Approach"
section, written before the v1/v2 question was settled and before the REST premise was
found to be false. It describes a design that did **not** ship; the README now describes
the one that did.

> 1. Compose: `louislam/uptime-kuma:latest` with volume `uptime-kuma:/app/data`.
> 2. `docker compose up -d`.
> 3. Wait for HTTP 200 on `/`.
> 4. First-run setup is interactive in v1 — need to either:
>    - Use the `uptime-kuma-api` Python lib to script setup (recommended)
>    - OR document a one-time manual setup step (breaks the "1-click" promise)
>    - OR check if Kuma v2 (currently beta) is mature enough — it has proper REST +
>      setup-via-env
> 5. After setup, create the Ntfy notification channel — POST monitor-notification with
>    `type: ntfy`, server: `homelabinfra_infra.notifications.ntfy_url`, topic: `homelab`.
> 6. Capture the notification channel ID.
> 7. Write to facts under the Shape B role key.
>
> `tasks/wiring/uptime-kuma.yml` targets the v2 REST surface behind a probe and skips with
> a warning when `GET /api/monitors` does not answer 200. Locking Kuma v1 here means
> replacing that file with a socket.io implementation.
>
> Implementation decision: lock in Kuma v1 + python lib, OR Kuma v2 if stable.

How it actually resolved: neither. Kuma 2.x shipped, the Python library was never needed,
and the REST surface named in step 7 does not exist in any version — `GET /api/monitors`
answers 200 `text/html` from the front end's catch-all route, which is exactly why the
probe, the delete and the verify-assert all passed against nothing. Slice 303 rebuilt the
wiring on socket.io.
