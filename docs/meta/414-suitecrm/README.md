# 414 — SuiteCRM

**Status:** open
**Subject:** CRM options
**Related:** 408 (app catalog and MariaDB provisioning), 300 (Caddy wiring), 406 (PBS backup)

## Goal

Deploy an instanced SuiteCRM 8 service for conventional sales, marketing and customer-service
workflows. The service uses a separately deployed MariaDB instance, runs every required scheduler
and asynchronous worker, stores generated administrator and database credentials in Vaultwarden,
and receives the platform's normal route, monitoring, removal and restore behavior. SuiteCRM is
the full-featured traditional CRM option, with its extensive module and customization surface
kept intact.

## Remaining

- [ ] Complete slice 408's MariaDB provisioning contract and select a SuiteCRM 8 release whose
      PHP, Apache and MariaDB versions match the current upstream compatibility matrix
- [ ] Select and prove the upstream-supported production installation path against this project's
      guest model before fixing the hosting kind in app defaults
- [ ] Add the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job with the backend instance named in `config/apps/<instance>.yml`
- [ ] Complete the CLI installer without exposing credentials, preserve those credentials on
      re-run, and configure both the scheduler cron job and Symfony Messenger worker
- [ ] Consume the platform SMTP contract for inbound and outbound mail workflows; keep extensions
      and application customization under operator control
- [ ] Wire the public route and monitor, and implement application-consistent backup and restore
      across SuiteCRM files plus MariaDB
- [ ] Prove account, contact, lead and opportunity workflows, a scheduled task, a second idempotent
      deploy, update, removal and cross-instance restore
- [ ] Pass both repository gates

## Links

- [SuiteCRM 8 installation](https://docs.suitecrm.com/8.x/admin/installation-guide/downloading-installing/) — production install, scheduler and worker requirements
- [SuiteCRM 8 compatibility matrix](https://docs.suitecrm.com/8.x/admin/compatibility-matrix/) — supported PHP, web-server and database versions
- `docs/meta/408-app-catalog/README.md` — SuiteCRM and MariaDB catalog rows
- notes.md — option assessment and implementation notes
