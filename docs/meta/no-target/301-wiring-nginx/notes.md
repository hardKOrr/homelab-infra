# 301 — notes

2026-07-25 — implementation complete; slice stays in-progress until live verification
against a deployed Nginx Proxy Manager.

What's in:

- `tasks/wiring/nginx.yml` — mints an NPM JWT (POST `/api/tokens`) unless
  `reverse_proxy.token` is pre-issued, lists `/api/nginx/proxy-hosts`, matches the
  proxy host by `domain_names` containing `wiring_domain`, then POSTs (absent) or PUTs
  (drifted forward_host / forward_port / forward_scheme / enabled). Verifies by
  re-listing.
- `tasks/unwiring/nginx.yml` — same auth, locate by domain, DELETE, verify the domain
  is no longer served.
- Both gated on `reverse_proxy.provider | default('none') == 'nginx'` per
  docs/specs/provider-noop-wiring.md.

Decisions:

- **Certificates.** `certificate_id` is only `new` (with `meta.letsencrypt_agree` +
  `letsencrypt_email`) when `reverse_proxy.letsencrypt_email` is set AND the proxy host
  is being created. On update the existing `certificate_id` is echoed back, so a re-wire
  never re-issues. With no LE email the route is created HTTP-only (`certificate_id: 0`,
  `ssl_forced: false`) — NPM's HTTP-01 challenge needs port 80 reachable from the
  internet, which most internal-only labs do not have, and a hard LE requirement would
  fail every deploy in those labs. The README's acceptance item "valid Let's Encrypt
  cert" therefore holds only for labs that configure the email.
- **Contract deviation from this slice's README** (same class as slice 300): the README
  referenced `homelabinfra_infra.nginx.api_url` / `.api_token` — the superseded
  service-keyed sketch. Implementation follows Shape B: `reverse_proxy.host` +
  `.port | default(81)` + `.token` / `.admin_user` / `.admin_password`. Those NPM-only
  fields are now documented in `ansible/vars/CONTRACT.md` §3.
- **no_log** on every authenticated request (they carry the JWT, per
  docs/specs/secrets-handling.md), with asserts on registered `.status` afterwards so
  failures stay diagnosable without printing the token.
- Payload is built as one dict expression rather than a YAML mapping of per-key
  templates, so `forward_port` stays an int and `ssl_forced` stays a bool in the JSON
  body (docs/specs/jinja-type-discipline.md). Verified by rendering it under ansible.

Verified: ansible-lint green (production profile, 75 files). Syntax gate's only failure
is the pre-existing slice-502 stub (`stacks/rollback-container.yml`, empty playbook).
Payload typing and the `selectattr('domain_names', 'contains', ...)` match verified with
a throwaway render. NOT verified live — acceptance needs a deployed NPM, which no app
slice provides yet (402 deploys Caddy; there is no NPM app slice). Flip to done after a
first real wire against NPM.
