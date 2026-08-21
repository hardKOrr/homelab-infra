# 415 — Twenty CRM

**Status:** open
**Subject:** CRM options
**Related:** 408 (app catalog, PostgreSQL and Redis provisioning), 300 (Caddy wiring), 406 (PBS backup)

## Goal

Deploy an instanced Twenty service as the modern, workflow-oriented CRM option. The application
uses separately deployed PostgreSQL and Redis instances, runs its server and worker processes,
persists uploaded files, stores application and backend credentials in Vaultwarden, and receives
the platform's normal route, monitoring, removal and restore behavior. The initial deployment is
single-workspace and does not enable code-execution features by default.

## Remaining

- [ ] Complete slice 408's PostgreSQL and Redis provisioning contracts before implementing this
      consumer; do not retain the database or cache sidecars from the upstream example Compose
      file
- [ ] Verify the current stable self-host release, server and worker topology, persistence paths,
      health checks and upgrade contract before fixing app defaults
- [ ] Add the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job with both backend instances named in `config/apps/<instance>.yml`
- [ ] Generate and preserve the application secret and initial administrator path without
      printing credentials; keep configuration shared consistently between server and worker
- [ ] Keep logic-function execution disabled unless the operator explicitly enables and selects
      a supported execution mode; consume the platform SMTP contract when mail is enabled
- [ ] Wire the HTTPS route and monitor, and implement application-consistent backup and restore
      across PostgreSQL plus persistent server storage
- [ ] Prove contact, company and opportunity workflows, one background workflow, file upload, a
      second idempotent deploy, update, removal and cross-instance restore
- [ ] Pass both repository gates

## Links

- [Twenty self-hosting](https://docs.twenty.com/developers/self-host/self-host) — supported self-host entry point
- [Twenty Docker Compose](https://docs.twenty.com/developers/self-host/capabilities/docker-compose) — server, worker, persistence, backup and reverse-proxy contract
- `docs/meta/408-app-catalog/README.md` — Twenty, PostgreSQL and Redis catalog rows
- notes.md — option assessment and implementation notes
