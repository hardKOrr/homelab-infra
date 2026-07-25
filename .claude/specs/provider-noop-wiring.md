# Spec: provider no-op wiring

<!-- isotope:section provider-noop-wiring:start -->

Every app deploy ends by registering with platform services; every removal mirrors it. Users
choose providers in `config/infrastructure.yml`; the platform must work with any subset.

## Rule

- Each wiring task is a per-provider file (`tasks/wiring/<provider>.yml`) selected by provider
  name; a provider configured as `none` (or absent from `homelabinfra_infra`) is a silent no-op,
  never an error.
- Every `tasks/wiring/<provider>.yml` has a matching `tasks/unwiring/<provider>.yml` that exactly
  inverses it; `apps/remove.yml` calls the unwiring set under the same conditions.
- Wiring tasks consume only the documented `wiring_*` variable contract and
  `homelabinfra_infra.*` — never app internals. The calling playbook must supply the contract
  variables from values visible in its own play scope (facts set on another host require
  `hostvars[]` or an `add_host` handoff; see architecture "Cross-play handoff" seam).
- Wiring is idempotent: re-running a wire task against an already-wired app changes nothing.

## Enforced by

- inspection — cite this spec in findings (contracts documented in each wiring file header)

<!-- isotope:section provider-noop-wiring:end -->
