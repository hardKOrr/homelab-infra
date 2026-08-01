# 015 — Wildcard DNS as the default path

**Status:** open
**Depends on:** none (docs, defaults and one prompt; no wiring logic changes)
**Blocks:** any honest claim that a lab on a consumer router is supported

## Problem

A homelab on a stock ISP router — GFiber, an Eero, a carrier gateway — has no DNS API.
The platform supports that lab perfectly well at the code level and tells it nothing.

Mechanically, `none` is already a clean no-op: all nine wiring call sites guard with
`homelabinfra_infra.dns.provider | default('none') != 'none'`, and `bootstrap-rundeck.sh`
already defaults `LAB_DNS` to `none`. Nothing is forced. **The defect is entirely in what
the project presents**, and it presents DNS automation as part of the core promise:

| Where | What it says |
|---|---|
| `README.md:6` | the headline promise — "…adds an uptime monitor and **creates its DNS record** — in one job" |
| `README.md:74` | Deploy Sonarr yields an app "…and **resolvable in DNS**" |
| `config.example/infrastructure.yml:dns` | ships `provider: opnsense` with a live IP — the example presumes an API-driven resolver |
| `bootstrap-rundeck.sh:521` | prompt reads `pihole \| adguard \| opnsense \| none` — three API providers and a bare escape hatch |
| *nowhere* | **what a `none` lab does instead.** "wildcard" appears once in the repo, in 407, about certificates |

So the consumer-router operator answers `none`, clicks Deploy Sonarr, gets a working Caddy
route at `sonarr.example.com`, and it does not resolve — with no error, because the no-op is
correct, and no document explaining the missing half. The one-click promise silently
delivers three of its four parts.

**The supported path is simpler than the automated one, which is why this is a
presentation bug and not a feature request.** One wildcard record — `*.<domain> A <proxy-ip>`
— created once by hand at the router or registrar, resolves every app the platform will ever
deploy. No API, no credential, no per-deploy DNS write, and it does not drift as apps come
and go. For most labs that is not a degraded fallback; it is fewer moving parts than
reconciling a record per app.

### Second defect, same seam: a declared provider with no way to credential it

`bootstrap-rundeck.sh` offers `opnsense` at the `LAB_DNS` prompt and writes `dns.provider`
and `dns.host`, but **never prompts for `dns.api_key` / `dns.api_secret`**, and no bootstrap
task writes them into `.generated/facts.yml`. `tasks/wiring/opnsense.yml:60-61` hard-asserts
both. So answering `opnsense` at that prompt authors a config that is guaranteed to abort at
the first app's wiring step.

The assert is correct and must stay — `specs/provider-noop-wiring.md` makes absent/`none` a
silent no-op, and a provider the operator explicitly declared but that cannot work should
fail loudly rather than ship apps with no DNS record and no signal. **The script is the
thing that is wrong**: it must collect the credential alongside the provider choice, exactly
as it already does for the Proxmox token. `ansible/vars/CONTRACT.md:189` compounds this by
marking `dns.api_key` "optional" when it is mandatory for every credentialed provider.

## Files

- `README.md:6,74` — stop promising an automatic DNS record unconditionally. State the two
  paths: one wildcard record (default), or a DNS provider that writes a record per app.
- `README.md` setup section — name the single record to create, before the reader hits it.
- `config.example/infrastructure.yml` — ship `provider: none` with the wildcard explained in
  place; demote `opnsense` to the commented alternative, with its `api_key`/`api_secret`
  uncommented as required-when-used.
- `rundeck/bootstrap-rundeck.sh:521` — reword the prompt so `none` reads as a real choice
  ("wildcard — one manual record") rather than an opt-out.
- `rundeck/bootstrap-rundeck.sh:522-524` — when a credentialed provider IS chosen, prompt for
  `api_key`/`api_secret` and stage them the way the Proxmox token is staged.
- `ansible/playbooks/apps/caddy.yml` — after the proxy's address is known, emit the exact
  record the operator must create (`*.<domain> A <proxy-ip>`) to the job log, and via
  `tasks/notify.yml` when notifications are configured.
- `ansible/vars/CONTRACT.md:189` — `dns.api_key` is required when `dns.provider` is a
  credentialed provider, not "optional".

## Approach

- **Reframe, do not rebuild.** No wiring task changes. The no-op path already works; this
  slice makes it the documented, named, defaulted path.
- **The platform already knows the answer.** Caddy's IP is in `homelabinfra_infra` by the
  time any app wires. Print the record rather than making the operator derive it.
- **A declared provider must be credentialable at the moment it is declared.** Provider
  choice and credential are one decision; splitting them across two layers is what produced
  the guaranteed-abort config.
- **Be honest about the limit of the zero-API story.** A real public domain still needs a
  registrar API token for Caddy's DNS-01 challenge (slice 407). Wildcard DNS removes the
  per-app record, not every credential. HTTP-01 or an internal CA is the true zero-API TLS
  path and should be named as such.

## Acceptance

- [ ] A reader on a stock ISP router can go from clone to a resolving app using only
      `README.md`, and the one manual record is stated before they need it
- [ ] `config.example/infrastructure.yml` defaults to the wildcard path
- [ ] The bootstrap prompt presents the wildcard path as a first-class choice
- [ ] Choosing `opnsense` (or any credentialed provider) at bootstrap collects its
      credentials, and a subsequent app deploy wires DNS successfully — no config that
      parses but cannot work
- [ ] A Caddy deploy names the exact wildcard record to create, in the job log
- [ ] `CONTRACT.md` states `dns.api_key` is required for credentialed providers

## Notes

Raised by the operator 2026-08-01, during the first from-scratch runner bootstrap, on
the observation that requiring a DNS provider and host up front "could be very rough if
we're requiring usage… what if someone doesn't run OPNsense and just the standard GFiber
or whatever router". The credential gap was found in the same session while choosing
`LAB_DNS` for that run; `none` was selected precisely because the credentialed path could
not have worked.
