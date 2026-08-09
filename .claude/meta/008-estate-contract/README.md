# 008 — Estate / multi-domain contract

**Status:** built
**Subject:** Config model
**Related:** 009 (identity modes build on estates), 407 (per-estate DNS-01), 200 (facts)

## Goal

The repo assumed exactly one domain: a single `domain:` scalar, a single `sso`/`dns` entry,
and every wiring domain `<instance>.<domain>`. A properly sanctioned second domain is not a
second TLS name — it is an **estate**: its own domain, its own Authentik, DNS and ACME
DNS-challenge token, sharing one Caddy/Ntfy/Kuma/metrics/PBS with the lab.

- `infrastructure.yml` grows an optional `domains:` map of named estates (`domain`,
  `default: true`, optional `dns_challenge`). The plain `domain:` scalar stays valid as
  shorthand for one default estate, so existing labs keep working unchanged.
- Apps pick an estate with `routing.estate`, defaulting to the default estate. A second
  estate's Authentik and Vaultwarden are ordinary app deploys with `routing.estate: <name>`
  — the one-click-per-app model, no bootstrap changes.
- Estate-bound facts (`sso`, optionally `dns`) live under `estates.<name>`; global services
  stay top-level. The default estate reads and writes top-level keys, so the single-domain
  path is byte-identical to before.
- `tasks/resolve-estate.yml` overlays estate-scoped facts onto `homelabinfra_infra` in every
  app playbook's Play 3 before wiring — **whole-key replacement, never recursive.**

## Remaining

Live acceptance needs a second-domain deploy; the lab runs one estate today.

- [ ] Single-domain lab: `facts.yml`, route JSON and Authentik objects identical before and
      after — no `domains:` map means resolve-estate no-ops
- [ ] Declaring `domains:` with a default estate changes nothing for apps without
      `routing.estate`
- [ ] Deploying Authentik with `routing.estate: <name>` writes `estates.<name>.sso` and
      leaves top-level `sso` untouched
- [ ] An app with `routing.estate: <name>` wires `<subdomain>.<estate-domain>` and registers
      against that estate's Authentik only
- [ ] An app naming an undeclared estate fails fast with the named assert

## Links

- `ansible/tasks/resolve-estate.yml`
- `ansible/tasks/bootstrap/write-generated-facts.yml` — optional
  `generated_facts_estate`; non-default estates merge under `estates.<name>.<role_key>`
- `ansible/playbooks/apps/*.yml` + `_template.yml` — resolve-estate pre_task,
  `wiring_subdomain` (from `routing.subdomain`, default instance) feeding `wiring_domain`
- `ansible/vars/CONTRACT.md` §3 (estates key), §5 (`domains:` map)
- `config.example/infrastructure.yml`, `config.example/apps/_template.example.yml`
