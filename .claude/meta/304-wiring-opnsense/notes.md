# 304 — notes

2026-07-25 — implementation complete; slice stays in-progress until live verification
against the lab's OPNsense.

What's in:

- `tasks/wiring/opnsense.yml` — searches host overrides
  (`POST /api/unbound/settings/searchHostOverride`), then `addHostOverride` (absent) or
  `setHostOverride/<uuid>` (drifted server or disabled), asserts `result == saved`, and
  calls `/api/unbound/service/reconfigure` only when something actually changed.
- `tasks/unwiring/opnsense.yml` — search, `delHostOverride/<uuid>`, reconfigure only on
  an actual delete.
- Both gated on `dns.provider | default('none') == 'opnsense'`; basic auth from
  `dns.api_key` + `dns.api_secret`.

Decisions:

- **Endpoint family.** The README sketched `unbound/host/addHost`; that controller was
  retired upstream. Implemented against `unbound/settings/*HostOverride`, current for
  OPNsense 23.1+.
- **Record target.** No app playbook sets `wiring_hostname` / `wiring_ip`, so the file
  derives them from the shipped contract: hostname = first label of `wiring_domain`,
  zone = the rest, and the address = `wiring_ip`, else the reverse proxy's IP (scheme
  and port stripped from `reverse_proxy.host`), else `wiring_upstream_host`. Pointing at
  the proxy is the correct default — apps are reached through it — and the explicit
  overrides remain available.
- **`reconfigure` is change-gated** because it restarts the resolver; running it on
  every idempotent re-wire would blip DNS for the whole lab.
- **`validate_certs` defaults to false** via `dns.validate_certs`: OPNsense ships a
  self-signed certificate and the call stays inside the lab network. Documented in the
  file header and in CONTRACT.md §3 so a lab with a trusted cert can turn it on.
- **`dns.host` may be a bare IP.** `config.example/infrastructure.yml` documents it that
  way for external, non-inventory hosts, which contradicts Shape B's "every host carries
  a scheme". Rather than break the documented example, the DNS wiring tasks prepend
  `https://` (OPNsense) when the scheme is missing; noted in CONTRACT.md §3.
- OPNsense form fields are strings even in JSON (`enabled: '1'`), which is why that
  payload deliberately does not use booleans.

Verified: ansible-lint green (production profile); hostname/zone/IP derivations verified
with a throwaway render. NOT verified live — acceptance (resolvable override, no
duplicates on re-wire, clean delete, change-gated reconfigure) needs the real firewall
and an API key/secret pair in `config/.generated/facts.yml`.
