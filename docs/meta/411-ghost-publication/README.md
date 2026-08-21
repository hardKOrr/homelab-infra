# 411 — Ghost publication hosting

**Status:** open
**Subject:** wife-friendly website hosting
**Related:** 408 (app catalog and database provisioning), 300 (Caddy wiring), 406 (PBS backup)

## Goal

Deploy an instanced Ghost publication for writing, newsletters and memberships. The app uses a
separately deployed MySQL 8 instance, stores owner, database and mail credentials in Vaultwarden,
and receives the platform's normal public route, monitoring, backup, removal and restore behavior.
Ghost is the preferred option when publishing is the product; it is not the general-purpose site
builder and must not be pointed at the MariaDB backend.

## Remaining

- [ ] Add MySQL 8 to slice 408's Batch B provisioning contract and complete that backend before
      implementing this consumer; Ghost does not support MariaDB in production
- [ ] Select and prove one upstream-supported production installation path against this project's
      guest model before fixing the hosting kind in app defaults
- [ ] Add the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job with the backend instance named in `config/apps/<instance>.yml`
- [ ] Complete unattended owner creation, preserve generated credentials on re-run and store them
      only in the canonical Vaultwarden item
- [ ] Consume the platform SMTP contract for transactional mail; treat Mailgun newsletter delivery
      as an optional, explicit provider configuration
- [ ] Wire the public route and monitor, and implement application-consistent backup and restore
      across Ghost content plus MySQL
- [ ] Prove editor publishing, media upload, a second idempotent deploy, update, removal and
      cross-instance restore
- [ ] Pass both repository gates

## Links

- [Ghost documentation](https://ghost.org/docs/) — publication, editor and self-hosting contracts
- [Ghost breaking changes](https://ghost.org/docs/changes/) — supported Node.js and database versions
- `docs/meta/408-app-catalog/README.md` — Ghost and MySQL catalog rows
- notes.md — option assessment and implementation notes
