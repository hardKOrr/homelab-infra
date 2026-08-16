# 305 — notes

2026-07-25 — implementation complete; slice stays in-progress until live verification
against a Pi-hole v6.

What's in:

- `tasks/wiring/pihole.yml` — opens a session (`POST /api/auth` with the app password),
  reads `/api/config/dns/hosts`, deletes any stale `<ip> <fqdn>` element for this FQDN,
  PUTs the wanted element when absent, verifies, and always closes the session.
- `tasks/unwiring/pihole.yml` — same session handling; deletes every element matching
  the FQDN regardless of address, verifies none remain.
- Both gated on `dns.provider | default('none') == 'pihole'`.

Decisions:

- **v6 detection.** `POST /api/auth` returning 404 is the v5 signal; the assert emits
  "Pi-hole v6+ required: … (v5 has no REST API)" and a separate message for a rejected
  password. That is this slice's acceptance item 4, and it costs one request that is
  needed anyway.
- **Session hygiene.** Pi-hole allows only a handful of concurrent sessions; a leaked
  SID locks the operator out of the UI. Both files put the record work in a block with
  an `always:` that DELETEs `/api/auth`, so a mid-run failure still releases the session.
- **Stale-record sweep.** Records are array elements keyed by the whole `<ip> <fqdn>`
  string, so an upstream IP change would otherwise leave two elements for one FQDN and
  Pi-hole would answer with both. Wire deletes non-matching elements for the FQDN first;
  unwire deletes all of them. This is why the match is a regex on `\s<fqdn>$` rather
  than an equality test.
- **Record target** follows the same derivation as slice 304: `wiring_ip`, else the
  reverse proxy IP, else `wiring_upstream_host`.
- `dns.api_key` carries the Pi-hole app password (v6 has no separate API key), and
  `dns.validate_certs` defaults to false; both documented in CONTRACT.md §3. Bare
  `dns.host` gets `http://` prepended (Pi-hole's default listener), where 304 uses https.
- no_log on every request (password and SID).

Verified: ansible-lint green (production profile); the FQDN match and URL-encoding of
the `<ip> <fqdn>` element verified with a throwaway render. NOT verified live — the
homelab uses OPNsense (slice 304), so this path needs a Pi-hole v6 to sign off.
