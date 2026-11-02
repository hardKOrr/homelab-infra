# Estates, public DNS and per-estate DNS

**`domains:` — named estates (optional).** An estate is a **separate** domain scope: its
own domain, its own SSO, its own DNS and ACME DNS-challenge material, and its own
applications. Treat estates as independent by default and share only what is named as
shared. The plain `domain:` scalar stays valid as shorthand for one default estate, so
existing labs are untouched.

**What is shared is an explicit, named list — not a residue.** `catalog/applications.yml`
requires a `scope` on every application, and it has exactly two values:

| `scope` | Meaning | Instance name |
|---|---|---|
| `estate` | one deployment per estate | `<app>-<estate>[-<variant>]` |
| `lab` | one deployment serves every estate | `<app>[-<variant>]` |

`lab` is the deliberate exception, and each catalog entry must record why the application
crosses the estate boundary. There is no default value: an unclassified application is
rejected at render time rather than landing silently on the shared side.

```yaml
domains:
  <default-estate>:
    domain: home.example.com
    default: true                  # required when two or more estates exist
  <another-estate>:
    domain: another.example.com
    dns_challenge:                 # optional — per-estate ACME DNS-01 (caddy role)
      provider: cloudflare         # any github.com/caddy-dns/<provider> module
      api_token: "..."             # provider-specific fields retain native names;
                                   # scoped to THIS domain; referenced only from its
                                   # own TLS policy (third deliberate secret exception,
                                   # alongside dns.host and vaultwarden.admin_token)
    public_dns:                    # optional; opt-in public apex and wildcard only
      enabled: false               # absent or false leaves existing behavior unchanged
      # provider: cloudflare       # defaults to this estate's dns_challenge.provider
    dns:                           # optional — which DNS provider serves THIS estate
      provider: opnsense           # pihole | adguard | opnsense | none
      host: 192.0.2.1            # non-secret half only; see below

public_dns:                        # lab-wide; one WAN address for every enabled estate
  ttl: 300
  families: [ipv4]                 # list ipv6 to publish AAAA too
  allow_private: false             # otherwise private, loopback, link-local and CGNAT are refused
  address:
    source: lookup                 # static | lookup | cname; required when an estate enables it
    # static: address: 203.0.113.10
    # lookup: urls: [<https-echo-url-1>, <https-echo-url-2>]
    # cname: target: ddns.operator.example.net
```

Public DNS is independent of application routing and creates no per-app records or
WAN port forwards. The lab has one WAN behind one firewall, so the address settings are
lab-wide in top-level `public_dns`, like the Caddy guest itself; each estate only opts in
and names its provider. It is off unless that estate sets `public_dns.enabled: true`; the
DNS-01 provider credentials already loaded for that estate are reused, with no second
token in `public_dns`. Before Caddy can publish records, a read-only preflight checks the
address from the Caddy guest and lists A, AAAA, and CNAME records for the estate apex and
wildcard through its provider API. Different same-type records fail with their type and
name shown and value redacted unless they already carry homelab-infra's Cloudflare
ownership comment or `adopt_existing: true`; an A/AAAA versus CNAME conflict always fails.
Private, loopback, link-local and CGNAT addresses are refused unless `allow_private: true`,
which prints a warning. Inbound ports remain an explicit `firewall.inbound` choice.

The preflight currently has a read adapter for Cloudflare and fails closed for providers
without one. The Cloudflare API token must allow Zone Read and DNS Edit. Records created
after an empty preflight are marked with the Cloudflare comment `Managed by homelab-infra
public DNS`; the preflight uses that marker to recognize homelab-infra's records when an
address later changes. A pre-existing matching record is allowed but is not marked or
adopted automatically.

Caddy's dynamic DNS app keeps `static` and `lookup` records current. When a provider write
fails it logs the error but does not retry until the WAN address changes or the Caddy
service restarts (a config reload is not enough). Lab Status reports that estate as
`stale`; restart Caddy on the proxy guest to publish again. A `cname` source uses Caddy's
static DNS records app to reconcile the apex and wildcard CNAMEs through each estate's
provider, with no address polling.

The Caddy role reads the shared address settings from top-level `public_dns` and each
estate's opt-in and optional provider beside its `dns_challenge`. It reuses that challenge's
provider-specific credential fields when it renders the Caddy provider block; it does not
load or create a second token. A config with no enabled estate renders neither public DNS
app and keeps prior behavior.

Caddy resolver precedence is policy-local: `domains.<estate>.dns_challenge` uses
its own options over guest-DNS defaults, without inheriting another estate's
options or the legacy instance block. For the single/default-domain policy,
`reverse_proxy.dns_challenge` recursively overrides legacy
`config/apps/<instance>.yml` `app.dns_challenge`; provider-specific fields retain
their native names. Explicit resolver lists replace defaults, not append to them.
Empty lists are rejected rather than permitting an implicit external fallback.

A `domains:` map with two or more entries must declare exactly one `default: true`.
Declaration order never decides identity. Estate-scoped instances use
`<app>-<estate>[-<variant>]` for every estate, including the default estate. A
single-estate lab keeps the short `<app>[-<variant>]` form.

**`domains.<name>.dns` — DNS is selected per estate, and the credential is not
duplicated.** `infrastructure.dns.provider` is global, so without this block a lab
whose default estate is already served by hand-built records cannot turn platform DNS
on for a second estate without rewriting the first estate's zone as each of its apps is
next deployed. The block carries the NON-SECRET half only — `provider`, `host`,
`validate_certs` — and `tasks/resolve-estate.yml` overlays it on the credential the
estate already inherits (`homelab-infra/dns`, or `homelab-infra/estates/<name>/dns`
when that estate uses a different DNS server). One firewall serving two estates is one
stored credential and two provider declarations. It applies to the default estate too,
so `domains.<default>.dns: {provider: none}` holds the default estate out while another
estate's records are managed.

**Two vault items per estate, mirroring the global pair.** They must not be merged:

| Item | Field(s) | Feeds |
|---|---|---|
| `homelab-infra/estates/<name>/reverse_proxy` | `dns_api_token` | `domains.<name>.dns_challenge.api_token` — ACME DNS-01 for that estate's certificates |
| `homelab-infra/estates/<name>/dns` | `api_key`, `api_secret`, `token` | `homelabinfra_infra.estates.<name>.dns` — the DNS-record wiring credential |

The reverse-proxy and DNS items are separate because ACME DNS-01 and DNS record wiring can
use different providers and credential shapes. Do not merge them.

A secret authored after the one-time Vaultwarden Cutover reaches either item through
the **Store Secret** job (`playbooks/maintenance/store-secret.yml`). The cutover importer
runs only in Seed mode, which `lab-run.sh` refuses after the vault-mode marker is written.

Apps choose an estate with `routing.estate` (default: the default estate). A
non-default estate's Authentik is just another app deploy with
`routing.estate: <name>` — its `sso` facts land under `estates.<name>` ([§3](../CONTRACT.md#3-canonical-homelabinfra_infra-shape)).
