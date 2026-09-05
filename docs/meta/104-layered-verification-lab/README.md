# 104 — Layered, disposable verification lab

**Status:** open
**Subject:** CI test-layer architecture
**Related:** #8 (baseline Actions gate, completed; not duplicated by this slice)

## Goal

Extend the hosted Actions gate from #8 — repository consistency, no credentials, no
Proxmox access — with graduated test layers, so each platform behavior (sanitized
Ansible semantics, Docker-hosted app roles, Kubernetes roles, Proxmox/provider API
contracts, secrets and destructive-operation boundaries, and an optional isolated
real-Proxmox lane) is tested at the cheapest credible boundary. This slice tracks the
epic (issue #29) and its authority boundary; each child issue owns its own
implementation and, where non-trivial, its own `docs/meta/` slice.

Hosted layers never gain live-lab authority: no production config, Vaultwarden
material, DNS tokens, media data, or Proxmox credentials reach Actions. A green hosted
run is fixture, container, or Kubernetes evidence, never live-lab acceptance — see
`docs/meta/README.md`'s `built` vs. `done` distinction. Any real-Proxmox lane is
optional, isolated to a dedicated test-only PVE lab, and deletes only resources
carrying the exact homelab-infra ownership tag.

## Remaining

- [ ] #30 — Sanitized lab fixtures and semantic Ansible validation (fixture
      prerequisite for #31 through #34).
- [ ] #31 — Container role integration harness for Docker-hosted apps (needs #30).
- [ ] #32 — Stateful Proxmox and provider API contract tests (needs #30).
- [ ] #33 — Selected application Docker and Kind smoke-test matrix (needs #31, #32).
- [ ] #34 — Secrets and destructive-operation safety boundaries.
- [ ] #35 — Optional isolated real-Proxmox acceptance lane (needs #30, #32, #34, and an
      approved isolated PVE lab).

## Links

- `gate/README.md` — the existing baseline gate this slice extends, not replaces.
- `docs/architecture.md` — Ansible UI-independence boundary that new layers must respect.
- `ansible/tasks/proxmox/` — the Proxmox boundary a contract-test layer targets.
- Child issues #30–#35 own their own `docs/meta/` slices once their implementation PRs
  land; link them here as they are created.
