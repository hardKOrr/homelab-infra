# 410 — WordPress website hosting

**Status:** open
**Subject:** wife-friendly website hosting
**Related:** 408 (app catalog and MariaDB provisioning), 300 (Caddy wiring), 406 (PBS backup)

## Goal

Deploy an instanced WordPress site with a block theme and the Site Editor as the normal authoring
surface. The app uses a separately deployed MariaDB instance through the Batch B provisioning
contract, stores generated administrator and database credentials in Vaultwarden, and receives
the platform's normal public route, monitoring, backup, removal and restore behavior. This is the
general-purpose option when the site needs forms, plugins, commerce or broad theme choice.

## Remaining

- [ ] Complete slice 408's MariaDB provisioning contract before implementing this consumer
- [ ] Add the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job with the backend instance named in `config/apps/<instance>.yml`
- [ ] Complete unattended first boot, create the initial administrator without printing its
      credential, store it in the canonical Vaultwarden item and make re-runs preserve it
- [ ] Install a maintained block theme as the usable default; leave optional plugins and site
      content under operator control
- [ ] Wire the public route and monitor, and implement application-consistent backup and restore
      across uploads plus the separately managed database
- [ ] Prove visual page editing, media upload, a second idempotent deploy, update, removal and
      cross-instance restore
- [ ] Pass both repository gates

## Links

- [WordPress Site Editor](https://wordpress.org/documentation/article/site-editor/) — visual
  whole-site editing with a block theme
- `docs/meta/408-app-catalog/README.md` — WordPress and MariaDB catalog rows
- notes.md — option assessment and implementation notes
