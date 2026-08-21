# 409 — Publii static-site hosting

**Status:** open
**Subject:** wife-friendly website hosting
**Related:** 408 (app catalog), 300 (Caddy wiring), 406 (PBS backup)

## Goal

Deploy one static-site instance that Publii can publish to without server or Git knowledge.
Each instance owns a restricted upload credential, a persistent document root, a public Caddy
route, an Uptime Kuma monitor and PBS coverage. Publii runs on the editor's computer; the lab
hosts only the generated static files. This is the preferred first option for a brochure site,
portfolio or ordinary blog because the public site has no application runtime or database.

## Remaining

- [ ] Define a generic static-site contract with one instance per site, a persistent document
      root and a staging directory so an interrupted upload cannot replace the live site
- [ ] Implement a restricted SFTP publication path whose generated credential is stored in the
      instance's canonical Vaultwarden item and is never printed
- [ ] Add the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job; do not deploy Publii itself to the lab
- [ ] Wire the public route and monitor, and implement PBS backup and restore of the document root
- [ ] Prove a Publii sync, an atomic replacement, a second idempotent deploy and restore into a
      different instance
- [ ] Pass both repository gates

## Links

- [Publii](https://getpublii.com/) — desktop static CMS and publication client
- `docs/meta/408-app-catalog/README.md` — application catalog row
- notes.md — option assessment and implementation notes
