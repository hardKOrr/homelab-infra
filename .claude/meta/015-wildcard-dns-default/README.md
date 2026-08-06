# 015 — Caddy-first wildcard HTTPS bootstrap

**Status:** open — Caddy-first ordering and verified HTTPS are observed live 2026-08-03, but
**item 3 is disproved as written**: the lab issues one Let's Encrypt certificate *per
hostname* via DNS-01, not a single apex + wildcard certificate. See the acceptance list.
**Depends on:** 402 (Caddy app), 407 (Caddy DNS-01), 500 (bootstrap plays)
**Blocks:** 016 (Vaultwarden identities), 014 (Vaultwarden secret store), every honest
Vaultwarden or Authentik live-acceptance claim

## Problem

The live run deployed Vaultwarden before Caddy and proved only Vaultwarden's direct health
endpoint and admin-token sink. The web vault needs an HTTPS secure context, while the current
bootstrap neither establishes wildcard DNS/certificate trust first nor collects the DNS-01
credential its Caddy implementation expects. Authentik has the same unproved external-origin
boundary. A green bootstrap can therefore leave the control plane present but unusable.

## Goal

Make a serving wildcard HTTPS route the first platform service established after the
Rundeck runner. Vaultwarden and Authentik must be reached through that route before their
web vault, account bootstrap, API authentication, or SSO behavior is tested.

The fresh-lab order becomes:

1. create the runner and temporary bootstrap keys;
2. deploy Caddy;
3. establish wildcard name resolution for `*.domain` to Caddy;
4. issue and verify the selected wildcard certificate;
5. deploy and wire Vaultwarden through Caddy;
6. initialize Vaultwarden identities (016);
7. continue the remaining baseline services in Vault mode (014).

This replaces the current `Vaultwarden -> Ntfy -> Caddy` order. Caddy and its certificate
path must no-op cleanly when Ntfy is not present; notifications are not a bootstrap
dependency.

## Modes

Bootstrap asks for one explicit HTTPS mode and writes the matching config.

### Public certificate (`acme-dns01`)

- Collect the DNS provider, zone/domain, ACME email, and a zone-scoped DNS-edit token.
- Store the token as a password entry in Rundeck Key Storage, not in config files.
- Inject it only into the Caddy deploy through a secure Key-Storage-backed job option.
- Validate the selected `caddy-dns` module's real credential field; the present generic
  `api_token` shape is not assumed to work for every provider.
- Create or verify wildcard DNS and issue `domain` + `*.domain` certificates through DNS-01
  without exposing port 80.

### Private certificate (`internal`)

- Create or verify the wildcard record in LAN DNS, or emit the exact record the operator
  must add when the DNS service has no supported API.
- Export Caddy's root CA as a Rundeck execution artifact and print its SHA-256 fingerprint,
  target trust stores, and exact installation commands.
- Stop before Vaultwarden account setup until the runner trusts that CA and an HTTPS probe
  through the public hostname succeeds.

`none` remains a valid DNS automation provider. It means "emit and verify the required
manual wildcard record," not "continue with an unresolvable hostname."

## Automation and manual handoff

The bootstrap performs every supported action itself. When a provider or client trust store
cannot be changed safely, it exits at a named checkpoint with a non-secret handoff containing:

- exact DNS record name, type, and value;
- certificate mode and expected issuer;
- CA artifact path and fingerprint for internal mode;
- a copy/paste verification command;
- the exact job or command to resume.

No output contains a DNS API token, certificate private key, or other credential.

## Files

- `rundeck/bootstrap-rundeck.sh` — collect HTTPS mode and public inputs; stage the DNS token
  in Key Storage; deploy Caddy before Vaultwarden; support resumable checkpoints.
- `rundeck/jobs/bootstrap.yaml` and the Caddy job — expose only the required
  Key-Storage-backed secure option.
- `ansible/playbooks/bootstrap.yml` — order Caddy before Vaultwarden and gate every later
  service on the HTTPS preflight.
- `ansible/playbooks/apps/caddy.yml`, `ansible/roles/caddy/` — consume the selected mode,
  reconcile wildcard policy, and verify certificate issuance without logging credentials.
- `ansible/tasks/wiring/caddy.yml` — wire Vaultwarden immediately after deployment and verify
  its external HTTPS origin.
- `config.example/infrastructure.yml`, `config.example/apps/caddy.example.yml` — keep provider
  choice and identifiers only; replace inline token examples with Key Storage paths.
- `rundeck/README.md` and root documentation — describe both modes and the manual checkpoint.

## Acceptance

- [x] A fresh bootstrap deploys Caddy before Vaultwarden, with no Ntfy dependency —
      observed 2026-08-03: execution 12 runs Caddy (15:40:38) before Vaultwarden (15:41:01)
      before Ntfy (15:42:08)
- [x] `https://vaultwarden.<domain>/alive` succeeds from the runner with hostname and
      certificate verification enabled before identity bootstrap starts — verified from
      the runner with verification on (no `-k`): HTTP 200, `ssl_verify_result=0`, Let's
      Encrypt issuer, valid 2026-08-03 → 2026-11-01. `https://ntfy.<domain>/v1/health`
      likewise
- [x] Public mode issues a certificate covering the wildcard via DNS-01 without exposing
      port 80 — **met 2026-08-06** (apex deliberately excluded, see Decisions). Caddy's
      store holds `wildcard_.<domain>`, `CN=*.<domain>`, issuer Let's Encrypt YE2, valid
      2026-08-06 → 2026-11-04. Six estate hostnames — vaultwarden, ntfy, auth,
      uptime-kuma, observability, pbs — all answer over HTTPS from the runner with full
      certificate verification (`ssl_verify_result=0`); `auth`, `uptime-kuma`,
      `observability` and `pbs` had never had certificates of their own. Two consecutive
      deploys (executions 23, 24) created no new per-hostname directory, and 24 converged
      to `changed=0` on the Caddy host. The original text is kept below because getting
      here cost three findings.

      **Original defect, 2026-08-03.** DNS-01 is genuinely in use
      (the ACME issuer carries a `dns` module and no port 80 is exposed), but Caddy's
      certificate store holds `vaultwarden.<domain>.crt` and `ntfy.<domain>.crt` — two
      per-hostname certificates. No wildcard certificate was ever issued.

      The cause is a misreading of Caddy's JSON model. The automation policy does carry
      `subjects: ["*.<domain>", "<domain>"]`, but in Caddy that list *selects which managed
      names this policy governs*; it does not cause one wildcard certificate to be
      obtained. Caddy manages a certificate for each site address it actually serves, so
      naming each app's hostname as its site address yields one certificate per app.
      Obtaining a real wildcard requires a site whose host *is* `*.<domain>`.

      Consequences, and why this is worth fixing rather than redefining: every app deploy
      now performs its own DNS-01 challenge, so each one depends on the DNS provider API
      being reachable at deploy time, and the lab accumulates certificates against Let's
      Encrypt's 50-per-registered-domain-per-week limit. A homelab adding apps one at a
      time will not hit the limit, but the failure mode is silent until it does, and the
      per-deploy DNS dependency is exactly what a wildcard was chosen to avoid.

      **Fixed in code 2026-08-06, unobserved.** `roles/caddy` now requests `*.<domain>`
      per estate through Caddy's `automate` certificate loader
      (`apps.tls.certificates.automate`), which is the thing that asks for a name;
      the DNS-01 policy's `subjects` keeps its existing job of selecting which policy —
      and therefore which estate token — governs that wildcard. Caddy 2.10+ then serves
      every subdomain from it: `TLS.Manage` skips issuing for a subject already covered by
      a managed wildcard, and `managingWildcardFor` consults the automate list for that
      coverage. Apps issue nothing at wire time.

      **Two further findings from the live run, both worth keeping.**

      *The propagation check cannot work in a homelab.* The wildcard would not issue:
      Caddy wrote the TXT record to Cloudflare — confirmed present through the provider
      API — then timed out "waiting for record to fully propagate". The lab redirects all
      outbound port 53 to its own resolver, so `dig CH TXT id.server @1.1.1.1` answered
      `"OPNsense.home.arpa"`, as did a query aimed at the zone's own Cloudflare
      nameserver. The configured `resolvers` were decorative. That resolver held a
      negative cache entry for `_acme-challenge.<domain>` with the zone's 1800s SOA
      minimum, against a 120s propagation window. Per-hostname certificates had escaped
      this by ordering luck — a first-ever query for `_acme-challenge.<app>` happened to
      land after the record existed. So the role now defaults `propagation_timeout: -1`
      with a 30s `propagation_delay`: Let's Encrypt validates from the public internet,
      where the record genuinely is. The operator separately exempted the Caddy guest
      from the DNS redirect, after which the wildcard issued in 41 seconds.

      *The cutover unloads working certificates.* Adding the wildcard to `automate` drops
      every per-hostname name from Caddy's managed set immediately — which also unloads
      their existing, valid certificates. On a lab that is already serving, that takes
      HTTPS down from the moment the config applies until the wildcard is obtained, and
      it is what the deploy gate detected rather than prevented: the gate runs after the
      config is written. A fresh lab never sees this, because Caddy is deployed before
      any app and holds no certificates yet. **Migrating an already-serving lab is a
      one-time outage window** — see the open item below.
- [ ] **Migration to the wildcard does not interrupt an already-serving estate.** Raised
      by the 2026-08-06 outage above. The fix is a two-phase reconcile: write the
      wildcard into `automate` while pinning the currently-managed hostnames in
      `automatic_https.force_automate`, so their certificates stay loaded; wait for the
      wildcard; then drop the pin in a second config write. Fresh labs are unaffected,
      so this gates migrations only
- [ ] Internal mode exports the CA, reports its fingerprint, and pauses until runner trust is
      verified
- [ ] A provider with no supported API receives an exact wildcard-DNS handoff and a resumable
      checkpoint rather than a misleading success
- [ ] Re-running after the manual action resumes at verification and does not rotate the
      certificate, DNS token, or Caddy identity
- [x] No DNS credential or certificate private key appears in config, logs, artifacts, or
      generated facts — `config/.generated/facts.yml` has no `dns_challenge`, no API token
      and no private key; the execution-12 log has none either

## Decisions taken 2026-08-06

- **Wildcard only, no apex, in the automate list.** Each automate name is a separate
  certificate; a wildcard does not cover the apex, and nothing in the baseline serves it.
  The policy `subjects` still carry the apex, so a future apex route issues under the same
  DNS-01 token without a config change.
- **The Caddy deploy blocks until each estate wildcard is on disk** (30 × 10 s, then a
  named assert). Under the old model a broken DNS-01 credential surfaced on the app that
  tripped over it; now nothing issues at wire time, so an estate whose challenge never
  completes would otherwise appear as an unexplained TLS error on some later app.
- **Pre-015 per-hostname certificates are left in place.** They fall out of Caddy's
  managed set, stop renewing, and expire unused. Deleting them is not worth a task.
- **Propagation checking is off by default** (`propagation_timeout: -1`,
  `propagation_delay: 30s`), overridable per estate. A homelab is the one place the
  question "can I see the record I just wrote?" cannot be answered honestly — the lab
  domain is captured by the local resolver and outbound :53 is commonly redirected. The
  role's own gate, which waits for the certificate to appear on disk, is a stronger check
  because it verifies the outcome rather than an intermediate step.

## Decisions

- **HTTPS is a prerequisite, not wiring polish.** Vaultwarden's web vault requires a secure
  context, and Authentik's browser behavior is evaluated only through its HTTPS origin.
- **Wildcard is the bootstrap DNS contract.** Per-app DNS writes may remain, but a single
  wildcard record is sufficient to bring up the platform control plane.
- **Manual work is a checkpoint.** Unsupported DNS and client trust changes produce exact,
  verifiable output and an explicit resume action.
