# 002 — Reconcile `config.example/*.yml` schema with `homelabinfra_config` namespace

**Status:** open
**Depends on:** 000, 001
**Blocks:** 004 (proxmox key naming acceptance), any user attempting the documented workflow

## Problem

The example files users copy into `config/` have top-level keys that the loader (slice 001) places under specific paths in `homelabinfra_config`. The current example files don't match what slice 001 expects:

- [config.example/proxmox.yml](../../config.example/proxmox.yml) has `proxmox:`, `networks:`, `ansible:` at top level — correct for slice 001's loader (which keeps them at top level under `homelabinfra_config`).
- [config.example/infrastructure.yml](../../config.example/infrastructure.yml) has `domain:`, `reverse_proxy:`, `sso:`, etc. at top level — correct for slice 001's loader (which wraps the whole file under `homelabinfra_config.infrastructure`).

So structurally the files are *almost* right under the slice 001 plan. But:

1. `proxmox.host` / `proxmox.port` keys are stale (slice 004 fixes).
2. The `vaultwarden:` block in `infrastructure.yml` adds keys (`admin_token`, `instance`) that don't fit the "providers and roles only" doctrine stated at the top of the same file. Either the doctrine is wrong, or `vaultwarden:` should move.
3. `networks:` in `homelabinfra-defaults.yml` is `null` — that means even after merge, a user who didn't override `default` loses it. Either defaults need a real `networks.default`, or example must be marked as required.
4. `vars/user-vars-example.yml` is a *third* schema (wrapped in `homelabinfra_config:`, contains a `proxmox.api_host:` that drifts from the example). Decide whether to keep this for back-compat (slice 001 keeps the loader path) or delete it; either way, sync or remove.

## Files

- `config.example/proxmox.yml` — already mostly right; coordinate with slice 004 for key rename
- `config.example/infrastructure.yml` — decide on the `vaultwarden:` block placement
- `ansible/vars/user-vars-example.yml` — sync or delete (decision needed)
- `ansible/vars/homelabinfra-defaults.yml` — populate `networks:` with a real default subtree, or document that user must define at least one network

## Approach

1. Lock in slice 001's loader as authoritative.
2. Sweep example files against the loader's expected shapes — confirm each top-level key in each file lands where slice 000's contract says it lands.
3. Decide on `vaultwarden:` — keep in `infrastructure.yml` (admit it's an exception to the "no connection details" doctrine, with a comment explaining why) or move to a separate `config/secrets.yml` (more files for the user, less ambiguous doctrine).
4. Decide on `user-vars-example.yml` — keep for `user_vars_file=` smoke tests, or delete and route tests through `config/*.yml` instead.
5. Populate `homelabinfra-defaults.yml` `networks:` with a sensible default or assert non-null in callers.

## Acceptance

- [ ] Every top-level key in `config.example/proxmox.yml` and `config.example/infrastructure.yml` has a documented landing point in `homelabinfra_config`
- [ ] A user copying both example files unchanged into `config/`, then running `load-user-vars.yml`, gets a fully populated `homelabinfra_config` with no orphan keys and no missing required keys (subject to filling in the obvious blanks like API tokens)
- [ ] `vaultwarden:` placement decision is documented in the example file's comment header
- [ ] `user-vars-example.yml` is either synced to current schema or deleted
- [ ] `homelabinfra-defaults.yml` `networks:` is either populated or its absence is asserted on with a friendly fail message
