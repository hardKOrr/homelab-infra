# 301 — Nginx Proxy Manager wire + unwire

**Status:** built
**Subject:** No live target
**Related:** 300 (the Caddy equivalent), 306 (the forward_auth half, also unverified)

## Goal

Support the `reverse_proxy.provider: nginx` choice; both halves were TODO headers. Both
drive NPM's REST API at `http://<host>:81/api`, gated on
`reverse_proxy.provider == 'nginx'`.

- **Wire** — authenticate (or use a pre-issued token), `GET /nginx/proxy-hosts`, match on
  `domain_names` containing `wiring_domain`, then POST or PUT a payload with
  `forward_host`/`forward_port`, `ssl_forced`, and a Let's Encrypt certificate.
- **Unwire** — find by domain, `DELETE /nginx/proxy-hosts/<id>`.

**This slice has no live target and is expected to stay `built` indefinitely.** The lab runs
Caddy; nothing here is verifiable until a second lab appears. That is not a stall.

One consequence worth knowing: 500 stages an import of `apps/nginx.yml`, which does not
exist — this slice shipped the wiring pair only, no app playbook.

## Remaining

All of it, pending an NPM lab.

- [ ] Wire creates a proxy host with a valid Let's Encrypt certificate
- [ ] Re-wire is idempotent
- [ ] Unwire deletes the host; idempotent on missing
- [ ] Tasks no-op for non-nginx providers

## Links

- `ansible/tasks/wiring/nginx.yml`, `ansible/tasks/unwiring/nginx.yml`
