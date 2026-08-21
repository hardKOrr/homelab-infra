# 416 — EspoCRM

**Status:** open
**Subject:** CRM options
**Related:** 408 (app catalog and MariaDB provisioning), 300 (Caddy wiring), 406 (PBS backup)

## Goal

Deploy an instanced EspoCRM service as the focused, lower-complexity CRM option. The application
uses a separately deployed MariaDB instance, runs its required daemon and optional WebSocket
process, persists application data and customizations, stores generated administrator and
database credentials in Vaultwarden, and receives the platform's normal route, monitoring,
removal and restore behavior.

## Remaining

- [ ] Complete slice 408's MariaDB provisioning contract before implementing this consumer; do
      not retain the database sidecar from the upstream example Compose file
- [ ] Verify the current official image, daemon and optional WebSocket topology, persistent volume
      set, health check and upgrade contract before fixing app defaults
- [ ] Add the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job with the backend instance named in `config/apps/<instance>.yml`
- [ ] Complete unattended initialization, preserve administrator and database credentials on
      re-run, and inject supported secrets through files rather than environment values where
      the official image permits it
- [ ] Consume the platform SMTP contract for application mail; keep extensions and UI-created
      customizations under operator control
- [ ] Wire the public route, monitor and optional WebSocket path, and implement
      application-consistent backup and restore across MariaDB plus all persistent application
      and customization data
- [ ] Prove account, contact, lead and opportunity workflows, a scheduled daemon task, a second
      idempotent deploy, update, removal and cross-instance restore
- [ ] Pass both repository gates

## Links

- [EspoCRM Docker installation](https://docs.espocrm.com/administration/docker/installation/) — official image, daemon, WebSocket and secret-file surface
- [EspoCRM backup and restore](https://docs.espocrm.com/administration/backup-and-restore/) — database and persistent-file recovery requirements
- `docs/meta/408-app-catalog/README.md` — EspoCRM and MariaDB catalog rows
- notes.md — option assessment and implementation notes
