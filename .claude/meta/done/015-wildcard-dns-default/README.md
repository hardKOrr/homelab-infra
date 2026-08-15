# 015 — Caddy-first wildcard HTTPS bootstrap

**Status:** closed 2026-08-15 — every acceptance item is either proven live or an
operator decision. The two boxes still unticked below (no-API-provider handoff, resume
after the manual action) are in INDEX's "Observe if it happens": this lab's domain is on
an API provider, so neither path can run here.
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

- [x] **Live acceptance: accepted by the operator, 2026-08-15, without forcing a second
      migration.** The two-phase reconcile is built: the role stages the wildcard together
      with affected route hostnames in `apps.tls.certificates.automate`, waits for the
      wildcard, then drops the temporary host pins in a second write. Fresh and
      already-converged labs skip the transition — and **this lab is converged**: Caddy
      168000010 holds `wildcard_.wasitacatisaw.cc.crt` with `automate:
      ["*.wasitacatisaw.cc"]`, so `_caddy_pending_wildcards` is empty and the transition
      path cannot fire on a re-run. Proving it live would mean deleting a working
      certificate to re-enter the migration deliberately; the operator declined that as not
      worth a live re-issue. **The code path is therefore built and reviewed but never
      executed** — treat it as "presumed broken" if a lab ever does migrate, per the
      standing rule in INDEX.md
- **Internal mode is declined, 2026-08-12** — the operator does not want Caddy's own CA
      ("just a PITA to deal with"): every client would have to be made to trust it. The code
      path stays for anyone who has no public domain, but **this lab will never enter it, so
      its criterion is not work and must not be listed as open.** The lab runs `acme-dns01`,
      proven 2026-08-06. Note this is about *who signs the certificate* — serving an app
      privately is `routing.access`, a different axis entirely, and works in either mode.
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
