# 008 — Estate / multi-domain contract

**Status:** closed 2026-08-15 — every acceptance item proven live on the foxglove estate,
at the cost of three defects the gates could not see. See notes.md.
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

**The second estate is live, 2026-08-15.** `config/infrastructure.yml` on the runner
carries `domains: {personal: wasitacatisaw.cc (default), foxglove:
foxglove-collective.com}`, the estate has its own Authentik (`authentik-foxglove`, LXC
168000200 on its own `sso_stack_foxglove` host) and an app was deployed into it and then
removed again. **Three defects were found doing it, all in code both gates passed and
nothing had ever executed** — see [notes.md](notes.md).

- [x] Single-domain lab: `facts.yml`, route JSON and Authentik objects identical before and
      after — no `domains:` map means resolve-estate no-ops. Same evidence as the row below
- [x] Declaring `domains:` with a default estate changes nothing for apps without
      `routing.estate` — **execution 146**, `Deploy Ntfy` with the map in place:
      `changed=0` on localhost and on ntfy, `facts.yml` byte-identical to the pre-run copy,
      and no `estates` key written
- [x] Deploying Authentik with `routing.estate: <name>` writes `estates.<name>.sso` and
      leaves top-level `sso` untouched — **execution 151**, then 156 after the defect below:
      `estates.foxglove.sso` = `authentik-foxglove` at `http://192.168.0.200:9000`, while
      top-level `sso` still reads `authentik` at `http://192.168.0.13:9000`
- [x] An app with `routing.estate: <name>` wires `<subdomain>.<estate-domain>` and registers
      against that estate's Authentik only — **execution 157**: `sabnzbd-foxglove` published
      at `https://sabnzbd-foxglove.foxglove-collective.com`, and querying **both** Authentik
      APIs shows the Application exists in the estate's instance and **not** in the
      platform's, whose twelve applications are unchanged
- [x] An app naming an undeclared estate fails fast with the named assert — scratch play on
      the runner, 2026-08-15: `routing.estate: nosuchestate` hit
      `Resolve estate | Assert a named estate is declared` with the message naming the
      missing `domains.nosuchestate` entry. The same play showed the foxglove overlay
      resolving to `domain=foxglove-collective.com` with `sso` replaced **whole** by
      `{provider: none}` — the default estate's Authentik host and token do not survive
      into the second estate, which is the property the non-recursive combine exists for

## Links

- `ansible/tasks/resolve-estate.yml`
- `ansible/tasks/bootstrap/write-generated-facts.yml` — optional
  `generated_facts_estate`; non-default estates merge under `estates.<name>.<role_key>`
- `ansible/playbooks/apps/*.yml` + `_template.yml` — resolve-estate pre_task,
  `wiring_subdomain` (from `routing.subdomain`, default instance) feeding `wiring_domain`
- `ansible/vars/CONTRACT.md` §3 (estates key), §5 (`domains:` map)
- `config.example/infrastructure.yml`, `config.example/apps/_template.example.yml`
