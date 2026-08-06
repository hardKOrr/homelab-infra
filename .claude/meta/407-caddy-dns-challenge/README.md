# 407 — Caddy per-estate DNS-01 challenge

**Status:** built — implementation complete; gate-verified; awaiting live acceptance (needs a real public domain + token)
**Depends on:** 402 (caddy role), 008 (estate contract)
**Blocks:** serving a public estate domain without exposing port 80

## Problem

The caddy role installs the stock apt binary — no DNS provider modules — and its
TLS automation is a single global policy (`acme` or `internal`). Serving public
domains properly needs DNS-01: per-estate provider modules and per-estate tokens,
each referenced only from its own domain's TLS policy ("properly sanctioned"
separation — one leaked token must not be able to issue for another estate).

## Files

- `ansible/roles/caddy/tasks/main.yml` — collect dns_challenge declarations
  (estates from `infrastructure.domains`, plus `app.dns_challenge` for the
  single-`domain:` shorthand), install missing `caddy add-package
  github.com/caddy-dns/<provider>` modules, emit one TLS automation policy per
  estate (`subjects: ['*.<domain>', '<domain>']`) ahead of the existing catch-all
  policy; token-carrying tasks are no_log-gated
- `ansible/vars/app-defaults/caddy.yml` — documented `app.dns_challenge` knob
- `config.example/apps/caddy.example.yml`, `config.example/infrastructure.yml`

## Known tradeoffs

- `caddy add-package` swaps the binary in place; the role's `apt state: latest`
  replaces it again on a later deploy and the module task re-adds it (one extra
  restart on upgrade deploys).
- Tokens land in `/etc/caddy/caddy.json` (0640 root:caddy) — same narrow
  guest-held-credential exception as the authentik compose env.

## Acceptance

- [ ] Lab with no dns_challenge anywhere: base config byte-identical (single
      catch-all policy), no module install attempted
- [ ] Estate with dns_challenge: policy list carries its subjects + provider +
      token ahead of the catch-all; the estate wildcard issues via DNS-01.
      **A policy's `subjects` never requested a certificate** — it only selects
      which names that policy governs. Slice 015 added the `automate` loader that
      asks for `*.<domain>`; this criterion is judged against that.
- [ ] Second estate's token appears only in its own policy
- [ ] Re-run is idempotent (module present → skip; policies undrifted → no PATCH)
