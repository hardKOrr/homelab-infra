# 015 — Caddy-first wildcard HTTPS bootstrap

**Status:** open
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

- [ ] A fresh bootstrap deploys Caddy before Vaultwarden, with no Ntfy dependency
- [ ] `https://vaultwarden.<domain>/alive` succeeds from the runner with hostname and
      certificate verification enabled before identity bootstrap starts
- [ ] Public mode issues a certificate covering the apex and wildcard via DNS-01 without
      exposing port 80
- [ ] Internal mode exports the CA, reports its fingerprint, and pauses until runner trust is
      verified
- [ ] A provider with no supported API receives an exact wildcard-DNS handoff and a resumable
      checkpoint rather than a misleading success
- [ ] Re-running after the manual action resumes at verification and does not rotate the
      certificate, DNS token, or Caddy identity
- [ ] No DNS credential or certificate private key appears in config, logs, artifacts, or
      generated facts

## Decisions

- **HTTPS is a prerequisite, not wiring polish.** Vaultwarden's web vault requires a secure
  context, and Authentik's browser behavior is evaluated only through its HTTPS origin.
- **Wildcard is the bootstrap DNS contract.** Per-app DNS writes may remain, but a single
  wildcard record is sufficient to bring up the platform control plane.
- **Manual work is a checkpoint.** Unsupported DNS and client trust changes produce exact,
  verifiable output and an explicit resume action.
