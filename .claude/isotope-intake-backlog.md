# Isotope intake backlog

Worklist for migrating the old `meta/` + `plans/backlog/` items into Isotope matter specimens.
**38 open items**: 30 open `meta/` slices + 8 `plans/backlog/` items. Nothing here is intaken yet.

## How to run (when resuming, via the operate nucleus)

For each item, capture it as a specimen from its source file:

```
python .isotope/bin/isotope.py agent open intake --mode capture --dump <source-path> --model sonnet
```

`agent open` must run through the Claude/Codex native adapter (it needs `ISOTOPE_HOST`), i.e.
launched by the operate nucleus, not a bare shell. Work the tiers in number order — the numbering
encodes dependencies (0XX foundation → 6XX UI). After a tier's specimens exist and are worked,
retire the corresponding `meta/` dirs; delete `plans/backlog/` files as each is intaken.

## Already done — do NOT intake (excluded)

`meta/` 000–004 are complete (in `plans/done/` + git history): variable-loading-contract,
config-loader, reconcile-config-example, filter-proxmox-module-params, proxmox-key-naming.
`plans/done/` (12 files) is completed-work provenance — history, not backlog.

## Tier 0XX — Foundation (variable plumbing; blocks downstream)

| Specimen slug | Type | Source dump | Goal |
|---|---|---|---|
| instance-config-schema | fix | `.claude/meta/005-instance-config-schema/README.md` | The two instance-config examples teach contradictory schemas — reconcile them. |
| generate-ip-combine | fix | `.claude/meta/006-generate-ip-combine/README.md` | `generate-ip.yml:75-86` bare `set_fact` clobbers `homelabinfra_instance` siblings — use `combine(recursive=True)`. |
| requirements-collections | fix | `.claude/meta/007-requirements-collections/README.md` | `requirements.yml` omits `community.docker`/`community.general` that the code already uses. |

## Tier 1XX — Hygiene (small fixes)

| Specimen slug | Type | Source dump | Goal |
|---|---|---|---|
| unattended-upgrades-dedupe | fix | `.claude/meta/100-unattended-upgrades-dedupe/README.md` | `configure-unattended-upgrades.yml` stub duplicates the inline impl in `guest-bootstrap.yml` — one source of truth. |
| stack-key-guard | fix | `.claude/meta/101-stack-key-guard/README.md` | App template calls find-or-create with `app_config.stack` unconditionally — guard with a friendly assert. |
| restart-tail-assert-order | fix | `.claude/meta/102-restart-tail-assert-order/README.md` | restart-app/tail-applog resolve `hosts: {{instance}}` before the assert runs — reorder so the friendly message shows. |
| find-or-create-host-docs | docs | `.claude/meta/103-find-or-create-host-docs/README.md` | Document the implicit `homelabinfra_config` state machine in find-or-create-host. |

## Tier 2XX — Bootstrap helpers

| Specimen slug | Type | Source dump | Goal |
|---|---|---|---|
| write-generated-facts | feature | `.claude/meta/200-write-generated-facts/README.md` | Implement `write-generated-facts.yml` (TODO stub) — everything downstream reads `homelabinfra_infra` from it. |
| configure-watchtower | feature | `.claude/meta/201-configure-watchtower/README.md` | Implement `configure-watchtower.yml` (TODO stub) — container auto-update day-2 promise. |
| configure-pbs | feature | `.claude/meta/202-configure-pbs/README.md` | Implement `configure-pbs.yml` (TODO stub) — backups day-2 feature. |

## Tier 3XX — Wiring (per-provider wire/unwire pairs)

| Specimen slug | Type | Source dump | Goal |
|---|---|---|---|
| wiring-caddy | feature | `.claude/meta/300-wiring-caddy/README.md` | Implement Caddy wire + unwire (TODO stubs) — default reverse proxy. |
| wiring-nginx | feature | `.claude/meta/301-wiring-nginx/README.md` | Implement Nginx Proxy Manager wire + unwire. |
| wiring-authentik | feature | `.claude/meta/302-wiring-authentik/README.md` | Implement Authentik wire + unwire — SSO. |
| wiring-uptime-kuma | feature | `.claude/meta/303-wiring-uptime-kuma/README.md` | Implement Uptime Kuma wire + unwire — auto-register each app. |
| wiring-opnsense | feature | `.claude/meta/304-wiring-opnsense/README.md` | Implement OPNsense Unbound wire + unwire. |
| wiring-pihole | feature | `.claude/meta/305-wiring-pihole/README.md` | Implement Pihole wire + unwire. |

## Tier 4XX — Apps (per-app role + playbook)

| Specimen slug | Type | Source dump | Goal |
|---|---|---|---|
| app-vaultwarden | feature | `.claude/meta/400-app-vaultwarden/README.md` | Vaultwarden role + playbook — enforced first baseline app (stores all secrets). |
| app-ntfy | feature | `.claude/meta/401-app-ntfy/README.md` | Ntfy role + playbook — bootstrap step 2, notification hub. |
| app-caddy | feature | `.claude/meta/402-app-caddy/README.md` | Caddy role + playbook — bootstrap step 3. |
| app-authentik | feature | `.claude/meta/403-app-authentik/README.md` | Authentik role + playbook — bootstrap step 4. |
| app-uptime-kuma | feature | `.claude/meta/404-app-uptime-kuma/README.md` | Uptime Kuma role + playbook — bootstrap step 5. |
| app-grafana | feature | `.claude/meta/405-app-grafana/README.md` | Grafana + Prometheus role + playbook — bootstrap step 6 (open: one stack or two apps). |
| app-pbs | feature | `.claude/meta/406-app-pbs/README.md` | PBS role + playbook — bootstrap step 7, full VM with installer ISO. |

## Tier 5XX — Top-level playbooks

| Specimen slug | Type | Source dump | Goal |
|---|---|---|---|
| bootstrap-plays | feature | `.claude/meta/500-bootstrap-plays/README.md` | `bootstrap.yml` should run all 7 baseline deploys in order (currently only config-load + assert). |
| app-remove-playbook | feature | `.claude/meta/501-app-remove-playbook/README.md` | Implement `apps/remove.yml` removal logic (TODO). |
| rollback-container | feature | `.claude/meta/502-rollback-container/README.md` | Implement `stacks/rollback-container.yml` — Watchtower feedback loop. |
| lab-status | feature | `.claude/meta/503-lab-status/README.md` | Implement `maintenance/status.yml` read-only report (currently a debug stub). |
| wire-media-stack | feature | `.claude/meta/504-wire-media-stack/README.md` | Implement `wire-<stack>.yml` app-to-app wiring (none exists). |

## Tier 6XX — UI job definitions

| Specimen slug | Type | Source dump | Goal |
|---|---|---|---|
| semaphore-project-json | feature | `.claude/meta/600-semaphore-project-json/README.md` | Create `semaphore/project.json` importable project (missing). |
| rundeck-jobs | feature | `.claude/meta/601-rundeck-jobs/README.md` | Create `rundeck/jobs/*.yaml` importable definitions (missing). |

## `plans/backlog/` — 8 additional open items (complement the meta slices; not duplicates)

| Specimen slug | Type | Source dump | Goal |
|---|---|---|---|
| remove-default-lxc-password | fix (security) | `.claude/plans/backlog/remove-default-lxc-password.md` | Drop `password: changeme` LXC default — every clone ships a known root password. |
| strip-secrets-from-guest-instance-json | fix (security) | `.claude/plans/backlog/strip-secrets-from-guest-instance-json.md` | Stop writing API token + root password into the guest instance JSON; fix path/perms. Relates to meta 003. |
| discover-dhcp-lease-ip | fix | `.claude/plans/backlog/discover-dhcp-lease-ip.md` | Discover a DHCP guest's real leased IP after boot instead of propagating literal `"dhcp"`. |
| fix-adhoc-playbook-env | fix | `.claude/plans/backlog/fix-adhoc-playbook-env.md` | Replace removed `yaml` stdout callback in ansible.cfg + add `netaddr` to gate reqs so real runs work. Coordinate with meta 007. |
| fix-app-template-wiring-facts | fix | `.claude/plans/backlog/fix-app-template-wiring-facts.md` | Play-3 `wiring_*` vars read Play-1 host-scoped facts undefined on localhost — resolve from reachable scope. |
| fix-check-native-updates-report-play | fix | `.claude/plans/backlog/fix-check-native-updates-report-play.md` | Report play must load `homelabinfra_infra` + fix the `tag_homelab-infra` group-name mismatch. |
| make-stack-host-docker-ready | fix | `.claude/plans/backlog/make-stack-host-docker-ready.md` | Created stack host needs valid hostname, keyctl/nesting, and Docker Engine installed. Relates to meta 006/103. |
| modernize-docker-apt-repo | fix | `.claude/plans/backlog/modernize-docker-apt-repo.md` | Replace deprecated `apt_key` with `signed-by` keyring; derive arch from `ansible_architecture`. |

## Notes

- **Dedup**: the `plans/backlog/` items reference related meta slices but are distinct findings
  (build.yml positioned plans as "covering findings meta does not"). Where a backlog item and a
  meta slice touch the same file (e.g. secrets items ↔ meta 003, docker-ready ↔ meta 006/103),
  intake both but coordinate the design so they don't collide.
- The `<!-- korr-groomer -->` placeholder sections in `plans/backlog/` files are empty — the old
  autobuild groomer never ran. Isotope's design reaction fills that role now.
