# 008 — Estate / multi-domain contract

**Status:** built — implementation complete and gate-verified; awaiting live acceptance
**Depends on:** 000, 001, 200
**Blocks:** any second-domain ("estate") deployment; 009 builds on it

## Problem

The repo assumed exactly one domain: `infrastructure.yml` has a single `domain:`
scalar, facts.yml a single `sso`/`dns` entry, and every wiring domain is
`<instance>.<domain>`. A properly sanctioned second domain is not a second TLS
name — it is an **estate**: its own domain, its own Authentik (and DNS, ACME
DNS-challenge token), sharing one Caddy/Ntfy/Kuma/metrics/PBS with the lab.

## Files

- `ansible/tasks/resolve-estate.yml` — new; overlays estate-scoped facts (domain,
  sso, dns — whole-key replacement, never recursive) onto `homelabinfra_infra` in
  every app playbook's Play 3, before wiring
- `ansible/tasks/bootstrap/write-generated-facts.yml` — optional
  `generated_facts_estate` input; non-default estates merge under
  `estates.<name>.<role_key>`
- `ansible/playbooks/apps/*.yml` + `_template.yml` — Resolve-estate pre_task;
  `wiring_subdomain` (from `routing.subdomain`, default instance) feeding
  `wiring_domain`; authentik playbook passes its estate to the facts write
- `ansible/vars/CONTRACT.md` §3 (estates key) and §5 (`domains:` map)
- `config.example/infrastructure.yml`, `config.example/apps/_template.example.yml`

## Approach

- `infrastructure.yml` grows an optional `domains:` map of named estates
  (`domain`, `default: true`, optional `dns_challenge`). The plain `domain:`
  scalar stays valid as shorthand for one default estate — existing labs and
  config.example keep working unchanged.
- Apps pick an estate with `routing.estate` (default: the default estate). The
  second estate's Authentik/Vaultwarden are ordinary app deploys with
  `routing.estate: <name>` — exactly the one-click-per-app model, no bootstrap
  changes.
- Estate-bound facts (`sso`, optionally `dns`) live under `estates.<name>`;
  global services stay top-level. Default estate reads/writes top-level keys, so
  the single-domain path is byte-identical to before.
- Per-estate ACME DNS-challenge tokens are consumed by the caddy role (slice 407).

## Acceptance

- [ ] Single-domain lab: facts.yml, route JSON and Authentik objects identical
      before/after (no `domains:` map → resolve-estate no-ops)
- [ ] Declaring `domains:` with a default estate changes nothing for apps without
      `routing.estate`
- [ ] Deploying Authentik with `routing.estate: <name>` writes
      `estates.<name>.sso` and leaves top-level `sso` untouched
- [ ] An app with `routing.estate: <name>` wires `<subdomain>.<estate-domain>`
      and registers against that estate's Authentik only
- [ ] An app naming an undeclared estate fails fast with the named assert
