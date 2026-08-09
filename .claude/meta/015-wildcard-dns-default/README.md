# 015 — Caddy-first wildcard HTTPS bootstrap

**Status:** open
**Subject:** Caddy / TLS
**Related:** 407 (per-estate DNS-01), 300 (Caddy wiring), 014 + 016 (what HTTPS unblocks)

## Goal

Make a serving wildcard HTTPS route the first platform service established after the
runner. Vaultwarden and Authentik must be reached *through* that route before their web
vault, account bootstrap, API authentication or SSO behaviour is tested — otherwise a green
bootstrap leaves the control plane present but unusable, which is what the earlier
`Vaultwarden → Ntfy → Caddy` order actually did.

The fresh-lab order: runner and bootstrap keys → Caddy → wildcard resolution for `*.domain`
→ issue and verify the certificate → Vaultwarden through Caddy → identities (016) → the
remaining baseline in vault mode (014). Caddy and its certificate path no-op cleanly
without Ntfy; notifications are not a bootstrap dependency.

Bootstrap asks for one explicit HTTPS mode — `acme-dns01` (public, zone-scoped DNS token in
Key Storage, no port 80) or `internal` (Caddy's own CA, exported and fingerprinted, pausing
until the runner trusts it). `none` remains valid and means "emit and verify the required
manual record", not "continue with an unresolvable hostname". Where automation cannot act
safely, bootstrap exits at a named checkpoint with a non-secret handoff and a resume
command. Mode details and the handoff contract are in notes.md.

## Remaining

- [ ] **Migration to the wildcard does not interrupt an already-serving estate.** Adding the
      wildcard to `automate` drops every per-hostname name from Caddy's managed set
      immediately, unloading valid certificates — HTTPS is down from the moment the config
      applies until the wildcard is obtained. The fix is a two-phase reconcile: write the
      wildcard while pinning current hostnames in `automatic_https.force_automate`, wait for
      the wildcard, then drop the pin in a second write. Fresh labs are unaffected, so this
      gates migrations only
- [ ] Internal mode exports the CA, reports its fingerprint, and pauses until runner trust
      is verified
- [ ] A provider with no supported API receives an exact wildcard-DNS handoff and a
      resumable checkpoint rather than a misleading success
- [ ] Re-running after the manual action resumes at verification and does not rotate the
      certificate, DNS token or Caddy identity
- [x] A fresh bootstrap deploys Caddy before Vaultwarden with no Ntfy dependency —
      execution 12, 2026-08-03
- [x] `https://vaultwarden.<domain>/alive` succeeds from the runner with full certificate
      verification before identity bootstrap starts — 2026-08-03
- [x] Public mode issues a certificate covering the wildcard via DNS-01 without exposing
      port 80 — **2026-08-06**, apex deliberately excluded. `CN=*.<domain>`, Let's Encrypt
      YE2. Six estate hostnames all verify; four had never had certificates of their own.
      Two consecutive deploys created no per-hostname directory and converged to
      `changed=0`. Getting here cost three findings — see notes.md
- [x] No DNS credential or certificate private key appears in config, logs, artifacts or
      generated facts

## Links

- `ansible/roles/caddy/`, `ansible/playbooks/apps/caddy.yml` — mode, wildcard reconcile,
  the on-disk certificate gate (30 × 10 s, then a named assert)
- `ansible/tasks/wiring/caddy.yml`
- `ansible/playbooks/bootstrap.yml` — Caddy before Vaultwarden; later services gated on the
  HTTPS preflight
- `rundeck/bootstrap-rundeck.sh`, `rundeck/jobs/bootstrap.yaml` — mode collection, Key
  Storage staging, resumable checkpoints
- `config.example/infrastructure.yml`, `config.example/apps/caddy.example.yml`
- [notes.md](notes.md) — mode contracts, the per-hostname certificate defect in full, the
  propagation-check finding, and the decision record
