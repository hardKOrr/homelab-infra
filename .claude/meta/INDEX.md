# Meta Index

Numbering scheme: `NNN` — first digit is **tier** (0 = highest priority, 6 = lowest), last two digits are order within the tier. See [README.md](README.md) for slice template and workflow.

## 0XX — Foundation (architecture/variable plumbing; nothing else runs until these work)

| # | Slice | Status | Depends on | Blocks |
|---|---|---|---|---|
| 000 | [Variable-loading contract (spec)](000-variable-loading-contract/README.md) | open | none | 001–004 and everything downstream |
| 001 | [Implement config/*.yml loader](001-config-loader/README.md) | open | 000 | 004, 200, every playbook importing load-user-vars.yml |
| 002 | [Reconcile config.example schema](002-reconcile-config-example/README.md) | open | 000, 001 | 004; any user attempting the documented workflow |
| 003 | [Filter proxmox module params](003-filter-proxmox-module-params/README.md) | open | 000 | any real LXC/VM provisioning |
| 004 | [Proxmox key naming unification](004-proxmox-key-naming/README.md) | open | 002 | all proxmox-touching work |
| 005 | [Instance config schema contradiction](005-instance-config-schema/README.md) | open | none | any real app deploy |
| 006 | [generate-ip combine](006-generate-ip-combine/README.md) | open | none | safe reuse of generate-ip |
| 007 | [requirements.yml collections](007-requirements-collections/README.md) | open | none | any docker app, guest-bootstrap |

## 1XX — Hygiene (small fixes, no architectural impact)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 100 | [unattended-upgrades dedupe](100-unattended-upgrades-dedupe/README.md) | open | none |
| 101 | [Stack key guard in template](101-stack-key-guard/README.md) | open | 005 |
| 102 | [Restart/tail assert ordering](102-restart-tail-assert-order/README.md) | open | none |
| 103 | [find-or-create-host docs](103-find-or-create-host-docs/README.md) | open | 006 |

## 2XX — Bootstrap helpers (tasks/bootstrap/* building blocks)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 200 | [write-generated-facts](200-write-generated-facts/README.md) | open | 004 |
| 201 | [configure-watchtower](201-configure-watchtower/README.md) | open | 200, 401 |
| 202 | [configure-pbs](202-configure-pbs/README.md) | open | 200, 406 |

## 3XX — Wiring (per-provider wire/unwire pairs)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 300 | [Caddy wire/unwire](300-wiring-caddy/README.md) | open | 200 |
| 301 | [Nginx wire/unwire](301-wiring-nginx/README.md) | open | 200 |
| 302 | [Authentik wire/unwire](302-wiring-authentik/README.md) | open | 200, 403 |
| 303 | [Uptime Kuma wire/unwire](303-wiring-uptime-kuma/README.md) | open | 200, 404 |
| 304 | [OPNsense wire/unwire](304-wiring-opnsense/README.md) | open | 200 |
| 305 | [Pihole wire/unwire](305-wiring-pihole/README.md) | open | 200 |

## 4XX — Apps (per-app roles + per-app playbooks)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 400 | [Vaultwarden](400-app-vaultwarden/README.md) | open | 004, 005, 200 (+000–003 foundation) |
| 401 | [Ntfy](401-app-ntfy/README.md) | open | 200, 400 |
| 402 | [Caddy](402-app-caddy/README.md) | open | 300, 401 |
| 403 | [Authentik](403-app-authentik/README.md) | open | 302, 401 |
| 404 | [Uptime Kuma](404-app-uptime-kuma/README.md) | open | 303, 401 |
| 405 | [Grafana + Prometheus](405-app-grafana/README.md) | open | 401 |
| 406 | [PBS](406-app-pbs/README.md) | open | 202, 401 |

## 5XX — Top-level playbooks

| # | Slice | Status | Depends on |
|---|---|---|---|
| 500 | [Bootstrap plays](500-bootstrap-plays/README.md) | open | 400–406 |
| 501 | [App remove playbook](501-app-remove-playbook/README.md) | open | 300–305 (unwire halves) |
| 502 | [Rollback container](502-rollback-container/README.md) | open | 201 |
| 503 | [Lab status](503-lab-status/README.md) | open | none |
| 504 | [Wire media stack](504-wire-media-stack/README.md) | open | 300–305 |

## 6XX — UI (Semaphore + Rundeck job definitions)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 600 | [Semaphore project.json](600-semaphore-project-json/README.md) | open | 500 |
| 601 | [Rundeck jobs](601-rundeck-jobs/README.md) | open | 500 |
