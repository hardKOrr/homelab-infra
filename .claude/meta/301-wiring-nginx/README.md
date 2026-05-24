# 301 — Nginx Proxy Manager wire + unwire

**Status:** open
**Depends on:** 200
**Blocks:** users who prefer NPM over Caddy

## Problem

Both `tasks/wiring/nginx.yml` and `tasks/unwiring/nginx.yml` are TODO headers. Required to support the `reverse_proxy.provider: nginx` choice.

## Files

- `ansible/tasks/wiring/nginx.yml` — implement
- `ansible/tasks/unwiring/nginx.yml` — implement

## Approach

NPM has a REST API at `http://<host>:81/api`. Token-based auth.

**Wire:**
1. Authenticate (POST /tokens with email+password) — or use pre-issued token from `homelabinfra_infra.nginx.api_token`.
2. GET `/nginx/proxy-hosts`, find existing by matching domain_names containing `wiring_domain`.
3. Build payload: domain_names, forward_host=`wiring_upstream_host`, forward_port=`wiring_upstream_port`, forward_scheme=http, ssl_forced=true, certificate_id=NEW (Let's Encrypt).
4. POST or PUT.

**Unwire:**
1. GET `/nginx/proxy-hosts`, find by domain.
2. DELETE `/nginx/proxy-hosts/<id>`.

Gated on `homelabinfra_infra.reverse_proxy.provider == 'nginx'`.

## Acceptance

- [ ] Wire creates a proxy host with valid Let's Encrypt cert
- [ ] Re-wire is idempotent
- [ ] Unwire deletes the host; idempotent on missing
- [ ] Tasks no-op for non-nginx providers
