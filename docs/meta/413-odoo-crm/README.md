# 413 — Odoo CRM

**Status:** open
**Subject:** CRM options
**Related:** 408 (app catalog and PostgreSQL provisioning), 300 (Caddy wiring), 406 (PBS backup)

## Goal

Deploy an instanced Odoo Community service with CRM as its initial application surface. The
service uses a separately deployed PostgreSQL instance, preserves its database and filestore as
one recovery unit, stores generated administrator and database credentials in Vaultwarden, and
receives the platform's normal route, monitoring, removal and restore behavior. Odoo remains the
broad business-suite option; deploying it must not silently enable or configure unrelated ERP
modules.

## Remaining

- [x] Confirm the Odoo Community modules that make up the supported CRM baseline and document
      material Community-versus-Enterprise limits without making Enterprise a deployment
      requirement. The baseline is Odoo 18 Community with the `crm` module only. Leads,
      opportunities, pipeline stages, activities and CRM reporting are included. Studio,
      VoIP, lead mining and advanced support features remain Enterprise-only limits and are
      not runtime dependencies.
- [x] Pair Odoo 18 with the named PostgreSQL 16 backend (`postgresql-odoo` by default).
      Odoo 18 supports PostgreSQL 12 and newer; PostgreSQL 16 is the repository baseline.
- [ ] Complete slice 408's PostgreSQL provisioning contract and verify the supported Odoo and
      PostgreSQL version pairing before fixing app defaults
- [ ] Add the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job with the backend instance named in `config/apps/<instance>.yml`
- [ ] Complete unattended database initialization, preserve the generated administrator and
      database credentials on re-run, and store them only in the canonical Vaultwarden item
- [ ] Consume the platform SMTP contract for application mail; keep optional modules and
      third-party add-ons under operator control
- [ ] Wire the public route and monitor, and implement application-consistent backup and restore
      across PostgreSQL plus the Odoo filestore
- [ ] Prove lead and opportunity management, a second idempotent deploy, update, removal and
      cross-instance restore
- [ ] Pass both repository gates

## Links

- [Odoo Community](https://www.odoo.com/page/community) — open-source business application suite
- [Odoo on-premise documentation](https://www.odoo.com/documentation/18.0/administration/on_premise.html) — supported deployment and PostgreSQL configuration surface
- `docs/meta/408-app-catalog/README.md` — Odoo and PostgreSQL catalog rows
- notes.md — option assessment and implementation notes
