# 407 — Caddy per-estate DNS-01 challenge

**Status:** built
**Subject:** Caddy / TLS
**Related:** 015 (the wildcard `automate` loader that actually requests certificates), 008

## Goal

The Caddy role installed the stock apt binary — no DNS provider modules — and its TLS
automation was a single global policy. Serving public domains properly needs DNS-01: **per-
estate provider modules and per-estate tokens, each referenced only from its own domain's
TLS policy**, so one leaked token cannot issue for another estate.

The role collects `dns_challenge` declarations (estates from `infrastructure.domains`, plus
`app.dns_challenge` for the single-`domain:` shorthand), installs missing
`caddy add-package github.com/caddy-dns/<provider>` modules, and emits one TLS automation
policy per estate ahead of the existing catch-all. Token-carrying tasks are `no_log`-gated.

Running live on the real domain; a second estate would close it.

**Two tradeoffs on record.** `caddy add-package` swaps the binary in place, so the role's
`apt state: latest` replaces it again on a later deploy and the module task re-adds it — one
extra restart on upgrade deploys. And tokens land in `/etc/caddy/caddy.json` (0640
root:caddy), the same narrow guest-held-credential exception as the Authentik compose env.

## Remaining

- [ ] A lab with no `dns_challenge` anywhere keeps a byte-identical base config — single
      catch-all policy, no module install attempted
- [ ] An estate with `dns_challenge` carries its subjects, provider and token ahead of the
      catch-all, and its wildcard issues via DNS-01. **A policy's `subjects` never requested
      a certificate** — it only selects which names that policy governs; slice 015 added the
      `automate` loader that asks for `*.<domain>`, and this criterion is judged against
      that shape
- [ ] A second estate's token appears only in its own policy
- [ ] Re-run is idempotent — module present skips, undrifted policies do not PATCH

## Links

- `ansible/roles/caddy/tasks/main.yml`
- `ansible/vars/app-defaults/caddy.yml` — the documented `app.dns_challenge` knob
- `config.example/apps/caddy.example.yml`, `config.example/infrastructure.yml`
- [../015-wildcard-dns-default/notes.md](../015-wildcard-dns-default/notes.md) — why
  propagation checking is off by default, which is a DNS-01 fact this slice inherits
