# 407 — Caddy per-estate DNS-01 challenge

**Status:** closed 2026-08-15 — three of four acceptance items proven live on the foxglove
estate; the fourth describes a lab this one cannot be.
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

**Proven live on a second estate, 2026-08-15 — executions 148 and 149.**

- [ ] A lab with no `dns_challenge` anywhere keeps a byte-identical base config — single
      catch-all policy, no module install attempted. **Not reachable in this lab** (both
      estates are on Cloudflare DNS-01), the same category as 306's Nginx path
- [x] An estate with `dns_challenge` carries its subjects, provider and token ahead of the
      catch-all, and its wildcard issues via DNS-01 — **execution 148**: the foxglove estate
      was declared and `wildcard_.foxglove-collective.com.crt` appeared on disk after four
      retries of the issuance gate (~40 s), matching the 41 s the first estate took. **A
      policy's `subjects` never requested a certificate** — it only selects which policy
      governs a name; slice 015's `automate` loader is what asks, and the running config now
      carries `automate: ["*.foxglove-collective.com", "*.wasitacatisaw.cc"]`
- [x] A second estate's token appears only in its own policy — read back from the running
      admin API: policy 0 governs `*.foxglove-collective.com` + apex, policy 1 governs
      `*.wasitacatisaw.cc` + apex, each with exactly one `api_token`, **different values**
      (compared by SHA-256 prefix, never printed), and the catch-all policy at index 2
      carries no DNS provider at all
- [x] Re-run is idempotent — **execution 149**, `changed=0` on the Caddy host: the module
      install skipped, neither policy PATCHed, the staging POST skipped and the temporary
      pin removal skipped

## Links

- `ansible/roles/caddy/tasks/main.yml`
- `ansible/vars/app-defaults/caddy.yml` — the documented `app.dns_challenge` knob
- `config.example/apps/caddy.example.yml`, `config.example/infrastructure.yml`
- [015/notes.md](../015-wildcard-dns-default/notes.md) — why
  propagation checking is off by default, which is a DNS-01 fact this slice inherits
