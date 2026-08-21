# 412 — Silex visual website builder

**Status:** open
**Subject:** wife-friendly website hosting
**Related:** 409 (static-site publication target), 408 (app catalog), 300 (Caddy wiring)

## Goal

Deploy Silex as the drag-and-drop WYSIWYG option. One private builder instance lets the editor
design sites visually, inspect or adjust HTML and CSS when useful, and export standards-based
static output. Published sites use slice 409's separate static-site instances, so the authoring
surface is protected by Authentik while each finished site remains public. Claude integration is
supported through an authenticated, non-public MCP path and is optional for ordinary editing.

## Remaining

- [ ] Complete slice 409's generic static-site publication target before implementing Silex
      publication into it
- [ ] Verify the current Silex production image, release pin, persistent storage, export behavior
      and upgrade contract before fixing app defaults
- [ ] Add the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job for the builder
- [ ] Protect the builder with Authentik and do not publish its MCP endpoint through the site's
      public route; store any Git or publication credentials in Vaultwarden
- [ ] Implement an explicit publish action from one Silex project into one named static-site
      instance without coupling the public site's availability to the builder
- [ ] Prove visual editing, responsive preview, static export, Claude-assisted CSS change, an
      idempotent builder deploy and recovery of both builder projects and a published site
- [ ] Pass both repository gates

## Links

- [Silex](https://www.silex.me/) — open-source visual builder for static sites
- [Silex self-hosting](https://docs.silex.me/en/dev/run) — Docker, persistence and export surface
- `docs/meta/409-publii-static-site/README.md` — public static-site target
- notes.md — option assessment and implementation notes
