# 419 — Per-estate mail identity

**Status:** built
**Subject:** estate-scoped outbound mail identity and optional SMTP credential
**Related:** 008 (estate contract), 418 (platform mail contract), [closed implementation issue #267](https://github.com/hardKOrr/homelab-infra/issues/267)

## Goal

An estate may declare `domains.<estate>.mail` with its own `from_address` and optional
`from_name`, while inheriting the global relay settings and shared Vaultwarden credential
by default. The declaration may also override non-secret relay fields for a different
SMTP endpoint. If that relay needs a separate login, the password is stored only in
`homelab-infra/estates/<estate>/mail` and selected only for that estate. The resolver
constructs one complete `homelabinfra_infra.mail` key before any mail-consuming role
renders SMTP settings, so estate applications send with the estate's From-domain.

## Remaining

- [x] 2026-09-18 — `domains.<estate>.mail` is documented and validated as a non-secret
      overlay; an estate without the block keeps the global mail block unchanged.
- [x] 2026-09-18 — estate resolution selects the shared relay credential or the selected
      estate's scoped password without consulting another estate's credential.
- [x] 2026-09-18 — mail-consuming roles resolve their estate before rendering SMTP values.
- [x] 2026-09-18 — focused gate fixtures cover identity selection, global inheritance,
      scoped-credential isolation, and application SMTP rendering.
- [ ] Observe a real estate application send through the configured relay and record the
      From-domain and authentication result in a self-contained follow-up observation issue naming the closed implementation issue #267 and linking this criterion.

## Links

- `ansible/tasks/resolve-estate.yml` — builds the effective estate mail registry entry
- `ansible/tasks/mail/resolve-mail.yml` — resolves the app estate before setting `wiring_mail`
- `ansible/scripts/config-doctor.sh` — validates global and estate mail declarations
- `ansible/vars/CONTRACT.md` — authoritative registry, config, and Vaultwarden shapes
- `config.example/infrastructure.yml` — example `domains.<estate>.mail` declaration
- `gate/test-config-loading.sh` — estate overlay and SMTP fact-shape regression tests
- `gate/test-mail-contract.sh` — mail contract and template regression tests
