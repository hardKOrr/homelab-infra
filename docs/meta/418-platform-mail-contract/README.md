# 418 — Platform mail contract

**Status:** built
**Subject:** the platform-wide outbound-SMTP contract every mail-sending app consumes
**Related:** 408 (Batch C ledger — mautic, acellemail, hi.events, paperless-ngx, nextcloud
and others need this before they can be implemented)

## Goal

Scope the `infrastructure.mail` provider contract that 408's Batch C ledger flagged as
unresolved: a mail provider configured once in `config/infrastructure.yml`, validated by
`config-doctor.sh`, with its credential owned exclusively by Vaultwarden
(`homelab-infra/mail`), and resolved by any app through the shared
`ansible/tasks/mail/resolve-mail.yml` seam — never as a private SMTP sidecar inside one
application. `mail.provider` is `smtp` (a generic relay) or `none`; a future
provider-specific extension (an API-based sender such as Mailgun or SES) adds optional
fields to this same block rather than inventing a second mail role key, mirroring how
`dns` adds `api_secret` for OPNsense.

## Remaining

- [x] Schema and validation — `mail.provider|host|port|from_address|encryption` in
      `ansible/vars/CONTRACT.md` §5 and `config-doctor.sh`; a `smtp` provider missing
      `host`/`port`/`from_address` is an ERROR, and `mail.password`/`api_key`/
      `api_secret`/`token` authored in tracked config is rejected outright
- [x] Vaultwarden credential ownership — `homelab-infra/mail` (`password`), imported by
      `vaultwarden-cutover.yml` from `homelabinfra_infra.mail.password`; a Seed-mode
      `LAB_MAIL_PASSWORD` env var covers the gap before cutover, mirroring
      `LAB_DNS_API_KEY`/`LAB_DNS_API_SECRET`
- [x] Registry wiring — `load-user-vars.yml` overlays the authored provider and
      Seed/vault credential onto `homelabinfra_infra.mail`, the same three-stage shape
      documented for `dns` (authored provider -> Seed env credential -> vault credential)
- [x] Per-app injection contract — `ansible/tasks/mail/resolve-mail.yml` resolves one
      `wiring_mail` fact (`enabled, host, port, encryption, from_address, from_name,
      username, password`), asserts a configured provider carries a usable credential,
      and is `no_log: true` throughout; consumers check `wiring_mail.enabled` before
      writing their own SMTP settings. No app template was changed — implementing one
      app's mail settings is explicitly out of this issue's scope (see the issue's
      Exclusions)
- [x] Redaction — the password matches `secret-shape.py`'s existing `password` pattern,
      so it is already rejected from generated facts; no code change was needed there
- [ ] A disposable-mailbox send test and a credential-rotation test against a real relay
      — blocked on the issue's own "Live-system authority" gate: the approved provider
      account, sender domain, test mailbox and authority must be named before any live
      test runs against a real relay, and none has been supplied yet
- [ ] `rundeck/bootstrap-rundeck.sh` gains an interactive `mail.provider` prompt (mirroring
      its existing DNS-provider prompt) that writes `LAB_MAIL_PASSWORD` to
      `/etc/homelab-infra/secrets.d/mail.env` — deferred; the contract works today with
      the credential supplied by hand or by a future bootstrap change, and touching the
      ~1800-line interactive script without a way to exercise its prompt flow here was
      judged higher-risk than the contract itself
- [ ] The first Batch C row that actually needs mail (mautic, acellemail, hi.events,
      paperless-ngx, nextcloud, ...) proves this contract end-to-end from its own
      app-defaults and role

## Links

- `ansible/vars/CONTRACT.md` — canonical `mail` shape, field table, Vaultwarden item
- `ansible/scripts/config-doctor.sh` — `mail.provider` validation
- `ansible/tasks/load-user-vars.yml` — registry overlay (authored -> Seed -> vault)
- `ansible/playbooks/maintenance/vaultwarden-cutover.yml` — `homelab-infra/mail` import
- `ansible/tasks/mail/resolve-mail.yml` — the per-app injection seam
- `config.example/infrastructure.yml` — documented `mail:` block
- `docs/meta/408-app-catalog/README.md` — the ledger row this slice resolves
- notes.md — scope evidence
