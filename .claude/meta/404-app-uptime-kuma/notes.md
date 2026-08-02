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
