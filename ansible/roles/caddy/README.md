# Caddy public DNS

Public DNS is opt-in per estate with `domains.<estate>.public_dns.enabled: true`.
The lab-wide `public_dns` block supplies one address source, record TTL, and enabled
IP families; each enabled estate uses its own DNS-01 provider configuration and
credentials for its apex (`@`) and wildcard (`*`) records. See the
[configuration contract](../../vars/CONTRACT.md).

For `static` and `lookup` sources, the role installs the pinned
[`caddy-dynamicdns`](https://github.com/mholt/caddy-dynamicdns/tree/67d107a42c0279f6e6d58a6afe646745c8b44f78)
module and configures one `dynamic_dns` app with a provider entry per enabled estate.
Its JSON uses the `App` and `Provider` fields from the pinned
[`dynamicdns.go`](https://github.com/mholt/caddy-dynamicdns/blob/67d107a42c0279f6e6d58a6afe646745c8b44f78/dynamicdns.go)
and the `Static` and `SimpleHTTP` fields from pinned
[`ipsource.go`](https://github.com/mholt/caddy-dynamicdns/blob/67d107a42c0279f6e6d58a6afe646745c8b44f78/ipsource.go):
`providers` entries contain `dns_provider` and `domains`, IP sources use `source`
with `ips` or `endpoints`, and `versions`, `exclude`, and `ttl` are app fields.
The app's source modules are `static` (`ips`), `simple_http` (`endpoints`), `interface`
(`name`), and `upnp` (`endpoint`). This role configures only `static` or `simple_http`.
Unless `allow_private: true`, it excludes RFC 1918, loopback, link-local, IPv6 ULA,
and CGNAT ranges from dynamic updates.

For a `cname` source, the role installs the pinned
[`caddy-host-dns`](https://github.com/SvenDowideit/caddy-host-dns/tree/ed6a941fc8cc511f19220f73ff8d5230c2a5cd0b)
module and reconciles one apex and one wildcard CNAME per enabled estate through its
DNS provider. This app has no address polling loop. Its configured records are
idempotent and are reconciled when the Caddy app is first loaded or its configuration
changes. The role compares the current app config first, so an unchanged deploy does
not call the provider or reload Caddy.
Its JSON uses the `App`, `Provider`, and `RecordSpec` fields in the pinned
[`dnsrec/app.go`](https://github.com/SvenDowideit/caddy-host-dns/blob/ed6a941fc8cc511f19220f73ff8d5230c2a5cd0b/dnsrec/app.go)
and [`dnsrec/records.go`](https://github.com/SvenDowideit/caddy-host-dns/blob/ed6a941fc8cc511f19220f73ff8d5230c2a5cd0b/dnsrec/records.go):
each `providers` entry contains `dns_provider` and `records`, with `name`, `zone`,
`type`, and `value` on each record; `ttl` and `best_effort` are app fields.

The dynamic DNS module uses `ip_sources`, `providers`, `versions`, `include`, `exclude`,
`check_interval`, and `ttl`. `@` and `*` are accepted together in one zone's domain
map, subject to that provider's wildcard support. It updates A and AAAA records at the
configured names; it does not have a record-ownership flag, so the public DNS
preflight must run before first publish. Provider `SetRecords` behavior may have
provider-specific effects at those same record names. The `static` IP source returns
the configured address, while the app still checks at its `check_interval` (30 minutes
by default); only the CNAME app has no polling loop.
Before Caddy can publish a public DNS app, the role runs a read-only preflight on the
Caddy guest. For `lookup`, it queries every configured HTTPS endpoint for each enabled IP
family, matching the pinned updater's address-family behavior; failed endpoints are skipped,
but successful answers for the same family must agree. It then lists A, AAAA, and CNAME at
each enabled estate's apex and wildcard through the estate's provider API. A different
same-type value is refused unless the record already has homelab-infra's Cloudflare
ownership comment or `adopt_existing: true`; an address/CNAME type conflict always fails.
Error messages name the record type and name while redacting its value and the provider
token.

The read-only provider adapter currently supports Cloudflare and fails closed for other
provider modules. After a successful preflight, records that were absent and Caddy creates
are marked through Cloudflare's record comment API as `Managed by homelab-infra public DNS`.
Existing equal records are allowed without being marked or adopted. Set
`allow_private: true` only when private, loopback, link-local, or CGNAT publication is
intentional; the deploy prints a warning when that override is used.

When a dynamic DNS provider write fails, the module logs the error without crashing
Caddy, but caches the attempted address. It tries again after the address changes or
after Caddy restarts. A config reload does not clear that state; restart the Caddy guest
to retry the same address.
