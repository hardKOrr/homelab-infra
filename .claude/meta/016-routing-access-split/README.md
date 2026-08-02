# 016 — Split routing.proxy into proxy selection and access

**Status:** done — implemented 2026-08-02, both gates green. Hard rename, no alias.
**Depends on:** 300 (Caddy wiring), 005 (instance-file schema)
**Blocks:** the public-edge end state — lab Caddy as the sole WAN edge

## Problem

`routing.proxy` meant two unrelated things at once.

`config.example/infrastructure.yml` documented it as **which** proxy serves the app, for a
two-proxy topology (`internal: caddy`, `external: nginx-ext`). `tasks/wiring/caddy.yml`
consumed the same value as `wiring_access`, where `internal` adds a `remote_ip` matcher
restricting the route to `reverse_proxy.internal_cidrs` and anything else omits it.

On a single-proxy lab — every lab this platform bootstraps — the two readings collide.
`routing.proxy: external` reads as "my other proxy handles this one", and there is no other
proxy, so what actually happens is that the app's route loses its source matcher and is
published to every address the proxy answers on. The operator's word for a topology choice
silently becomes an exposure decision, in the direction that removes a control.

This is not theoretical for the end state: lab Caddy becomes the sole WAN edge, and Plex
must reach remote friends. Once Caddy is on the WAN, "no source matcher" means the internet.
A flag that can publish an app as a side effect of naming a proxy cannot survive that.

## Files

- `ansible/tasks/wiring/caddy.yml` — `_caddy_access` reads exposure, valid set is now
  `internal | public`; contract header and both assert messages name `routing.access`
- `ansible/playbooks/apps/{vaultwarden,ntfy,caddy,authentik,uptime-kuma,observability,pbs}.yml`
  and `_template.yml` — `wiring_access` reads `routing.access`, not `routing.proxy`
- `ansible/playbooks/maintenance/configure-app.yml` — new `access` parameter, its own
  assert, its own `combine` into `_routing`; `proxy` is untouched
- `rundeck/jobs/configure-app.yaml`, `semaphore/project.json` — new `access` form field
- `ansible/vars/app-defaults/*.yml` (8) — explicit `access: internal` beside `proxy:`
- `config.example/apps/*.example.yml` (9), `config.example/infrastructure.yml` — documented
- `ansible/vars/CONTRACT.md` — the two axes stated; `internal_cidrs` row now cites `access`
- `README.md` — the source-networks bullet cites `routing.access`

## Approach

**Two axes, named for what they answer.** `routing.proxy` (`internal | external | none`)
answers *which* proxy. `routing.access` (`internal | public`, default `internal`) answers
*who may reach it*. Nothing about a proxy choice can widen access, and nothing about access
can move an app to a different proxy.

**`public`, not `external`.** Reusing `external` would have kept the collision alive in the
operator's head — the same word on two keys meaning two things. `public` states the
consequence rather than the topology, which is what someone editing an instance file at
speed needs to read.

**Hard cutover, no alias.** `routing.proxy: external` no longer widens access anywhere. The
only consumers are this repo's own playbooks and the live lab, which was being torn down the
same day; a compatibility alias would have preserved exactly the ambiguity the slice exists
to remove. Anyone carrying `proxy: external` to mean exposure now gets an internal route —
failing closed, and visibly, rather than silently continuing to publish.

**`access` is explicit in every app-default, not left to the default.** The value is
security-relevant, so `grep -rn 'access:' ansible/vars/app-defaults/` answers "what is
published?" without anyone having to know what the default is. Every baseline app is
`internal`, including Caddy, where it is inert while `proxy: none`.

**`routing.proxy` stays.** It is not vestigial: `playbooks/apps/caddy.yml` reads
`proxy != 'none'` to keep the reverse proxy from routing itself, and the two-proxy topology
it documents is still a supported shape.

## Acceptance

- [x] `tasks/wiring/caddy.yml` derives the `remote_ip` matcher from `routing.access` alone
- [x] Every app playbook and `_template.yml` passes `routing.access` as `wiring_access`
- [x] `Configure App` can set `access` from both Rundeck and Semaphore
- [x] Setting `routing.proxy: external` does not remove an app's source matcher
- [x] ansible-lint (production profile) and syntax-check both green
- [ ] Live: an app deployed with `routing.access: public` gets a route with no `remote_ip`
      matcher, and one with `internal` gets the matcher — observed against a real Caddy

## Notes

Raised by the operator 2026-08-02 while planning the public-edge cutover, and recorded in
project memory before that as "routing.access split — agreed to separate them, not yet
implemented". The naming and the no-alias decision were the operator's.

The live lab's routes were measured during the same session's teardown: all five surviving
routes (`authentik`, `uptime-kuma`, `pbs`, `vaultwarden`, `ntfy`) carried **no** `remote_ip`
matcher despite every app-default declaring `proxy: internal`. That lab was bootstrapped by
a script version that did not author `reverse_proxy.internal_cidrs` at all, which predates
the assert now guarding it — so this was not the overloaded flag firing. It is still the
sharpest available illustration of the failure mode: nothing about the deploy told anyone
those apps were unrestricted.
