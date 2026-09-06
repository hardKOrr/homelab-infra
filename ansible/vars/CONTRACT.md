# Variable loading contract

This is the authoritative variable-loading contract for the `homelabinfra_*` namespaces and config
files. Inspection rules that protect these shapes are in `docs/specs/config-layering.md` and
`docs/specs/namespace-merge-discipline.md`.

## 1. The three namespaces

- `homelabinfra_config.*` — merged user + defaults, the **input** layer (available at provision time).
- `homelabinfra_instance.*` — facts **computed at runtime** (IP allocation, vmid, etc.).
- `homelabinfra_infra.*` — the **service registry**: generated provider topology recursively
  overlaid in memory with Vaultwarden runtime fields.

`homelabinfra_config.infrastructure` and `homelabinfra_infra` are **not the same dict**:
`infrastructure.yml` feeds both — its provider *choices* merge into `homelabinfra_config.infrastructure`
(input layer), and those choices plus bootstrap-written endpoints/tokens land in `homelabinfra_infra`
(the registry).

## 2. Load map: file → wrapper → target key

| File | Wrapper in file | Loaded into | Notes |
|---|---|---|---|
| `vars/homelabinfra-defaults.yml` | `homelabinfra_defaults:` | `homelabinfra_config` (seed, lowest precedence) | unwrapped before merge |
| `config/proxmox.yml` | none (top-level `proxmox:`, `networks:`, `ansible:`) | `homelabinfra_config` (loader injects those three keys) | optional at load time; callers validate required keys |
| `config/infrastructure.yml` | none; the whole file is the infrastructure shape | `homelabinfra_config.infrastructure` | optional at load time; callers validate required keys |
| `vars/app-defaults/<app>.yml` | none | `app_config` (per-play app merge — see app-layering note) | separate merge, not part of `homelabinfra_config` |
| `config/apps/<instance>.yml` | none | `app_config` (per-play app merge) | whole file merges over `<app>_defaults` via `combine(recursive=True)` — see app-layering note |
| `config/.generated/facts.yml` | none | generated base of `homelabinfra_infra`; Vaultwarden runtime fields overlay it recursively | written incrementally by `write-generated-facts.yml` |
| `user_vars_file` (back-compat) | `homelabinfra_config:` | `homelabinfra_config` | legacy single-file path; already self-wrapping |

## 3. Canonical `homelabinfra_infra` shape

The registry is role-keyed and provider-agnostic. Consumers build derived values
(e.g. a notification URL) from `host` + `topic`; the registry never stores pre-built URLs.
Every HTTP-service `host` value is a full base URL **including scheme** (e.g.
`http://192.168.1.20`) — consumers concatenate paths onto it directly; consumers needing a
bare hostname (e.g. a shoutrrr URL) strip the scheme themselves. `databases.<instance>.host`
is the non-HTTP exception: it is a bare SSH and database address paired with `port`.

```yaml
# config/.generated/facts.yml topology, overlaid in memory with Vaultwarden fields
domain: homelab.example.com          # copied from infrastructure.yml
reverse_proxy: { provider, instance, host, port, internal_cidrs }
sso:           { provider, instance, host, admin_user }
notifications: { provider, instance, host, topic }   # NOT ntfy_url — consumers build {{ host }}/{{ topic }}
monitoring:    { provider, instance, host, admin_user, notification_id }
metrics:       { provider, instance, host, prometheus_host, admin_user }
dns:           { provider, host }
mail:          { provider, host, port, encryption, from_address, from_name, username }
backups:       { instance, host, datastore, datastore_path, api_token_id }
vaultwarden:   { host, port }
kubernetes:    { instance, provider, version, host, ingress_vip, ingress_controller,
                 storage_class, storage_reclaim_policy, storage_path,
                 failure_domain_mode, nodes }
media:                               # optional — app-to-app wiring for the media stack
  <instance>: { app, host, config_path, ... }   # credentials overlay from Vaultwarden
databases:                           # optional — independently deployed database backends
  <instance>: { provider, host, port, client_hosts } # host is a bare SSH/database address
runner:                              # the host this platform runs FROM — see below
  { provider, instance, host, vmid, node, checkout_path, venv_path, branch }
estates:                             # optional — only when infrastructure.yml declares domains:
  <estate-name>:                     # non-default estates only; the default estate uses
    sso: { provider, instance, host }          # the top-level keys above
    dns: { provider, host }                    # optional — global dns serves the estate if absent
```

**`runner` — the host the platform runs from.** Written by
`rundeck/bootstrap-rundeck.sh`, not by any playbook, because the runner is created before
any playbook can run. It exists so playbooks and `status.yml` can name the host they are
executing on. `provider` is `rundeck`; `host` is the UI's base URL including
scheme; `checkout_path` and `venv_path` are the paths `lab-run.sh` resolves from
`/etc/homelab-infra/lab-run.env`; `branch` is the branch the checkout tracks and refreshes
to before every job. Nothing wires against this key — it is descriptive.

**Estate scoping.** `infrastructure.yml` may declare a `domains:` map of named
estates (§5); apps pick one with `routing.estate`. Estate-bound role keys (`sso`,
optionally `dns`) for a non-default estate are written under `estates.<name>` by
`write-generated-facts.yml` (`generated_facts_estate`) and overlaid onto the
top-level keys by `tasks/resolve-estate.yml` before wiring runs — replacement is
whole-key, never a recursive merge, so a default-estate token can never leak into
another estate's wiring. All other role keys (reverse_proxy, notifications,
monitoring, metrics, backups, vaultwarden) are global: one instance serves every
estate. The default estate (and every lab without a `domains:` map) reads and
writes only the top-level keys — unchanged behavior.

Provider-specific optional fields are written by the application that deploys the provider
and read only by that provider's wiring tasks:

| Role key | Field | Provider | Purpose |
|---|---|---|---|
| `reverse_proxy` | `token` | nginx | pre-issued NPM JWT; skips the login round-trip |
| `reverse_proxy` | `admin_user`, `admin_password` | nginx | NPM admin login, exchanged for a JWT per run |
| `reverse_proxy` | `letsencrypt_email` | nginx | when set, new proxy hosts request a LE certificate and force SSL; omit for internal-only labs |
| `reverse_proxy` | `dns_api_token` | caddy | DNS-01 credential, normally a Cloudflare token scoped to Zone Read plus DNS Edit for the one lab zone; stored only in Vaultwarden after cutover |
| `sso` | `admin_user`, `admin_password` | authentik | akadmin's generated sign-in credentials; nothing reads them, they are recorded so the operator can log in |
| `notifications` | `user`, `password` | ntfy | publish account; `configure-watchtower.yml` needs the basic-auth pair because shoutrrr authenticates that way |
| `notifications` | `token` | ntfy | publish-only access token on `topic`; every `uri`/`curl` consumer sends it as `Authorization: Bearer` |
| `monitoring` | `notification_id` | uptime_kuma | id of the Ntfy notification channel attached to every monitor. A hint, not a dependency: the wiring resolves the channel by NAME from the live instance and accepts this id only if that instance still has it, because a rebuilt Uptime Kuma reuses ids from 1 and attaching a stale one fails the monitor's foreign key after the monitor row is already written |
| `metrics` | `prometheus_host` | prometheus_grafana | loopback URL on the stack host; Prometheus is deliberately not routable, so this is for humans and on-host debugging |
| `metrics` | `admin_user`, `admin_password` | prometheus_grafana | Grafana's generated sign-in credentials |
| `monitoring` | `admin_user`, `admin_password` | uptime_kuma | generated sign-in credentials, and the credential the WIRING authenticates with: Uptime Kuma has no REST monitor API in any version, so monitors are socket.io events and only an admin sign-in can emit them. An API key cannot. Without these two fields every app deploy skips monitor registration |
| `backups` | `api_token_id`, `api_token_secret` | pbs | PBS API token the backup configuration authenticates with; PBS reveals a secret only at creation, so a lost secret forces token rotation |
| `dns` | `api_secret` | opnsense | second half of the OPNsense API key/secret basic-auth pair |
| `dns` | `api_key` | pihole | the Pi-hole app password, exchanged for a session SID (v6+ only) |
| `dns` | `validate_certs` | opnsense, pihole | default `false` — lab DNS hosts serve self-signed certificates |
| `mail` | `password` | smtp | SMTP AUTH password for `mail.username`; Vaultwarden-only, never authored |

`monitoring` is the role key for uptime monitoring. Do not use a provider-named
`uptime_kuma` key.

**`kubernetes` — the cluster hosting backend.** Written by
`playbooks/apps/k3s-cluster.yml`. Topology only: the cluster's credentials — the etcd join
token and the administrative kubeconfig — are canonical in the organization-owned
Vaultwarden item `homelab-infra/<instance>` and never appear here.

| Field | Notes |
|---|---|
| `host` | Kubernetes API base URL including scheme, addressed at the founding node |
| `ingress_vip` | the one stable internal ingress address; what the platform Caddy proxies to |
| `ingress_controller` | in-cluster controller terminating plain HTTP on the VIP (`traefik`) |
| `storage_class` | default StorageClass. The current `homelab-local-path` class is node-pinned: a pod whose volume lives on an unavailable node stays Pending rather than rescheduling |
| `storage_reclaim_policy` | `Retain` — only the removal job's explicit `delete_data` removes a volume |
| `storage_path` | the directory on each node that every volume lives under. Topology, not a secret. `maintenance/reclaim-volume.yml` refuses to delete a directory that is not under it, so the guard is inert while this is absent |
| `failure_domain_mode` | `distinct-nodes` when every server sits on a different Proxmox node, `single` otherwise. Consumers describing availability read this rather than counting nodes |
| `nodes` | the node declarations verbatim: name, Proxmox placement, address, sizing, taints |

**`media` — the app-to-app wiring registry.** Unlike every other role
key, `media` is instance-keyed rather than role-keyed: one entry per media app,
because a lab runs several Sonarrs and several download clients at once. It is
read by `playbooks/stacks/wire-media-stack.yml` and by nothing else.

| Field | Required? | Notes |
|---|---|---|
| `app` | required | app kind — a key of `media_wiring.kinds` in `ansible/vars/media-wiring.yml` (`sonarr`, `radarr`, `lidarr`, `readarr`, `prowlarr`, `bazarr`, `sabnzbd`, `qbittorrent`, `deemix`, `slskd`) |
| `host` | required | full base URL including scheme **and port** — the port is read off this value, not from a separate field |
| `api_key` | per kind | *arr apps, Prowlarr, Bazarr, SABnzbd, Slskd. Discoverable for *arr kinds — see below |
| `username`, `password` | qBittorrent | |
| `arl` | Deemix | |
| `url_base` | optional | reverse-proxy subpath |
| `enabled` | optional | default `true`; sets the download client's enable flag |
| `peers` | optional | instance names this app may wire to; default is every compatible app |
| `categories` | optional | `{<arr instance>: <category>}` — download clients only; default is the target app's `default_category` |
| `sync_level`, `sync_categories`, `anime_sync_categories` | optional | Prowlarr Application settings for an *arr entry |
| `migrated_from` | optional | full base URL **including port** this app answered on before migration, written by `migrate-servarr.yml`. The wiring locates a record by name, then by address, and this is the second address it will recognise — which is what turns a peer entry pointing at the source host into a repair of that entry rather than a duplicate beside it |

Entries may also be **discovered** rather than registered: an instance file
declaring `app.media_kind` joins the registry with its `app.host` (or the dynamic
inventory entry plus `app.port`), and an *arr's self-generated API key is read out
of `<app.config_path>/config.xml` at wire time. Registry entries win over
discovered ones. This is what lets a lab wire media apps it did not deploy itself,
before per-app media roles exist.

**`databases` — independently deployed data backends.** This registry is keyed by
backend instance, because a lab can deploy `postgresql`, `postgresql-immich`, and
`postgresql-forgejo` as separate recovery units. Applications select an instance in
their `database` configuration; generated facts contain topology only.

| Field | Notes |
|---|---|
| `provider` | backend implementation, initially `postgresql`, `mariadb`, `mysql`, `influxdb`, or `redis` |
| `host` | bare SSH and database address, not an HTTP URL; database consumers pair it with `port` |
| `port` | backend listener port; provider-specific, for example PostgreSQL `5432` or InfluxDB `8086` |
| `client_hosts` | MySQL/MariaDB only: account-host patterns allowed for provisioned application roles; never use `%` |

Ntfy ships with `auth-default-access: deny-all`, so notification consumers authenticate
when credentials are present. Credential fields remain optional for compatibility with an
existing Ntfy instance that permits anonymous publishing.
`dns.host` is the one `host` that may be a bare IP (`config.example/infrastructure.yml`
documents it that way for external, non-inventory hosts); the DNS wiring tasks prepend a
scheme when it is missing.

Do not use the superseded pre-built `notifications.ntfy_url` shape. The registry stores
`notifications.host` and `notifications.topic`; consumers build the URL.

**`mail` — the platform outbound-mail contract.** Always an external relay: this platform
never runs an SMTP server, so `mail.host` is a bare hostname like `dns.host` and is never
resolved from the Proxmox inventory. `mail.provider` is `smtp` (a generic SMTP relay — the
only shape implemented so far) or `none`. A provider-specific extension (an API-based
sending service such as Mailgun or SES) adds its own optional fields to this same block
the way `dns` adds `api_secret` for OPNsense, rather than inventing a second mail role key.
`mail.username` is the non-secret half of SMTP AUTH (often the same value as
`from_address`); the password never appears in tracked config or generated facts — it
lives only in `homelab-infra/mail`. Like `notifications`, mail has no per-record
external resource to create, so there is no `tasks/wiring/<provider>.yml` pair — an app
that sends mail includes the shared `ansible/tasks/mail/resolve-mail.yml`, which reads
`homelabinfra_infra.mail` and sets one `wiring_mail` fact
(`enabled, provider, host, port, encryption, from_address, from_name, username,
password`) with `no_log: true`. `wiring_mail.enabled` is `false` whenever
`mail.provider` is `none` or absent, and every consumer must check it before writing its
own SMTP settings — `resolve-mail.yml` does not configure any application itself.

## 4. Merge order (low → high precedence)

1. `vars/homelabinfra-defaults.yml` (unwrap `homelabinfra_defaults:`) → seed of `homelabinfra_config`.
2. `config/proxmox.yml` (loader injects `proxmox`/`networks`/`ansible` under `homelabinfra_config`).
3. `config/infrastructure.yml` (loader injects under `homelabinfra_config.infrastructure`).
4. `user_vars_file` if present (back-compat; already carries its own `homelabinfra_config:` wrapper).

All merges use `combine(recursive=True)`; later layers win per key.

## 5. Required vs optional keys per config file

### `config/proxmox.yml`

| Key | Required? | Default / notes |
|---|---|---|
| `proxmox.api_host` | required | canonical name |
| `proxmox.api_port` | optional | default `8006` (canonical name) |
| `proxmox.node` | required | |
| `proxmox.api_user` | required | |
| `proxmox.api_token_id` | required | |
| `proxmox.api_token_secret` | required *in file or runtime environment* | secret — omit it from the file after Vaultwarden cutover |
| `proxmox.validate_certs` | optional | default `false` — a stock Proxmox node is self-signed, so guest creation fails with `CERTIFICATE_VERIFY_FAILED` when this verifies. `inventory/proxmox.yml` assumes the same. Set `true` once the node serves a trusted certificate |
| `proxmox.nodes` | optional | `{node_name: address}` for every cluster node, written by `bootstrap-rundeck.sh` from `pvesh get /cluster/status`. Consumed by `tasks/proxmox/register-nodes.yml`, which makes `delegate_to: <node name>` resolvable — node names have no `ansible_host` from the dynamic inventory. Falls back to `proxmox.api_host` for the targeted node when absent |
| `proxmox.storage` | required *in practice* | lab-wide storage for every guest; written by `bootstrap-rundeck.sh` from the first active storage advertising content type `rootdir`. Falls back to `local`, which a stock node **cannot** hold a container on. No `vars/app-defaults/*` pins storage — it is a node fact, not an app fact. Per-app override: `proxmox.disk_volume.storage` (LXC), `proxmox.vm.storage` (VM) |
| `networks.<name>.cidr` | required | per named subnet, **after inheritance** — see below |
| `networks.<name>.gateway` | required | per named subnet, after inheritance |
| `networks.<name>.dns_servers` | required | per named subnet, after inheritance |
| `networks.<name>.bridge` | required | per named subnet, after inheritance |
| `networks.<name>.vlan` | optional | Proxmox VLAN tag; `0` or absent means untagged. One network per VLAN — the tag, subnet and gateway travel together, so an app changes VLAN by changing its `proxmox.network` name |
| `networks.<name>.reserved` | optional | list of spans never allocated: an address, `"a-b"`, or a CIDR. Independent of Proxmox — this is how a NAS, a switch or a router's DHCP range stays unallocatable |
| `networks.<name>.pools` | optional | `{<pool>: {range: "a-b"}}` or `{<pool>: {cidr: "..."}}`; each pool must sit inside `cidr`. A guest allocated into a pool never lands outside it, and an exhausted pool fails the run rather than spilling into the wider subnet |
| `networks.<name>.default_pool` | optional | pool used by a guest that names none and inherits none |
| `networks.<name>.ip_offset` | optional | fallback walk only (no pool applies). An index into the host range, not a last octet: at a /20, `10` is `x.0.10` and the range runs to `x.15.254` |
| `networks.<name>.max_hosts` | optional | fallback walk only: how far past `ip_offset` allocation may walk |

**Every named network inherits `networks.default`.** `tasks/network/generate-ip.yml` builds
the effective config as `networks.default | combine(networks.<selected>, recursive=True)`,
and `config-doctor.sh` requires the four keys above on that merged result. A band therefore
declares only what differs from `default` — legitimately one key.

That is what lets the estate split start before the VLANs do. `bootstrap-rundeck.sh` asks
for two ADDRESSES — where lab-wide services start and where the estate's apps start — and
writes `shared` and `<estate>` as offsets into one flat subnet, with `max_hosts` on the
lower band so allocation cannot walk into the upper one. Both sets are addressed apart from
the first deploy while sharing one bridge and one broadcast domain; segmenting later adds
`cidr`/`gateway`/`vlan` to a band that already exists, and **no guest already deployed
changes address**. Deferring the split instead means renumbering the whole lab.

Pool selection, highest first: `proxmox.pool` in the instance file (must exist, or the
run fails); the pool named after the app's `stack`, used only if the network defines it;
`default_pool`; otherwise the `ip_offset`/`max_hosts` walk. `proxmox.ip_address` in an
instance file pins an exact address — honoured as written or refused with the conflict
named, never quietly replaced. `ansible/scripts/allocate-ip.py` decides; every refusal
carries its reason.

**Network selection is by SCOPE, not by a name repeated in every instance file.**
`ansible/tasks/network/resolve-network.yml` is the one seam, and it answers in this order:

1. `proxmox.network` in the instance file, or `stacks.<name>.network` for a stack host —
   an explicit choice, which must name a declared network or the run fails.
2. `shared`, when the application is `scope: lab` in `catalog/applications.yml` or its
   stack declares `shared: true`.
3. The app's estate: `domains.<estate>.network` when that estate declares one, otherwise a
   network named after the estate itself.
4. `default`.

Steps 2 and 3 are ADVISORY — used only when `config/proxmox.yml` actually declares a
network of that name, the same asymmetry `pool_hint` uses. A lab with one flat `default`
network resolves to `default`. A lab segments one band at a time by declaring
`networks.shared`, then `networks.<estate>`, and
redeploying. No `vars/app-defaults/*.yml` pins `network:` — a name in git-managed defaults
would override this resolution in every lab at once.

The estate boundary is a network boundary because one network is one VLAN: the tag, the
subnet and the gateway travel together. What this platform does NOT do is create that VLAN,
its bridge, its routes, or the firewall rules that let `shared` reach each estate. Those are
the operator's, exactly like the gateway and the DHCP range. Declare here only what already
exists on the wire.
| `ansible.ssh_user` | required | |
| `ansible.ssh_public_key` | required | |

### `config/infrastructure.yml`

| Key | Required? | Default / notes |
|---|---|---|
| `domain` | required | shorthand for a single default estate |
| `domains` | optional | map of named estates — see below |
| `reverse_proxy.provider` | required | `caddy \| nginx \| none` |
| `reverse_proxy.instance` | required unless provider `none` | |
| `reverse_proxy.internal_cidrs` | required for Caddy internal routes | source CIDRs allowed to reach apps with `routing.access: internal` |
| `reverse_proxy.dns_challenge.provider` | recommended for Caddy ACME | `cloudflare` enables DNS-01 without public app records or WAN port forwarding; the token is external/Vaultwarden material |
| `reverse_proxy.dns_challenge.resolvers` | optional | public resolvers used for DNS-01 propagation checks; defaults to Cloudflare's public resolvers so split-horizon Unbound cannot mask the temporary TXT record |
| `sso.provider` | required | `authentik \| none` |
| `sso.instance` | required if provider `authentik`, else optional | |
| `notifications.provider` | required | `ntfy \| gotify \| discord \| none` |
| `notifications.instance` | required unless provider `none` | |
| `notifications.topic` | optional | |
| `notifications.webhook_url` | optional | |
| `dns.provider` | required | `pihole \| adguard \| opnsense \| none` |
| `dns.host` | required for external providers | not in Proxmox inventory |
| `dns.api_key` | optional | |
| `dns.instance` | optional | |
| `mail.provider` | optional, default `none` | `smtp \| none` — absent means disabled, exactly like an explicit `none`, so an existing checkout with no `mail:` block keeps passing `config-doctor.sh` unchanged |
| `mail.host` | required unless provider `none` | SMTP relay hostname; never in Proxmox inventory |
| `mail.port` | required unless provider `none` | typically `587` (STARTTLS) or `465` (implicit TLS) |
| `mail.encryption` | optional | `starttls \| tls \| none`; default `starttls` |
| `mail.from_address` | required unless provider `none` | the envelope/header From every app sends as |
| `mail.from_name` | optional | display name paired with `from_address` |
| `mail.username` | optional | SMTP AUTH username, often equal to `from_address` |
| `mail.password`, `mail.api_key`, `mail.api_secret`, `mail.token` | rejected | secret-shaped fields must never be authored here — `config-doctor.sh` fails the run; store the credential in `homelab-infra/mail` |
| `maintenance.boot.order` | optional | default Proxmox startup tier for a guest that declares no `proxmox.boot_order`; `50`. `none` disables boot ordering lab-wide and leaves `startup` unset on every guest |
| `maintenance.boot.up` | optional | seconds Proxmox waits after a guest before starting the next tier; `15` |
| `maintenance.schedule` | optional | when the lab may be DISRUPTED — `always`, `never`, or a window `{days, start, duration}`. Defaults to nightly 04:00 for 120 minutes. Update cadence is unaffected; only reboots and container restarts wait for it. `never` is notify-only |
| `domains.<estate>.network` | optional | the network — and therefore the VLAN — this estate's guests are addressed on, keyed into `config/proxmox.yml` `networks:`. Absent, the estate's own name is tried and then `default`; see the network-selection order above |
| `domains.<estate>.maintenance.schedule` | optional | that estate's own window. Estates are independent clocks — a schedule is never merged or inherited ACROSS estates |
| `stacks.<name>.network` | optional | the network this stack's host is addressed on. Without it a stack declaring `shared: true` resolves to `shared`, and an estate-scoped stack to its estate's network |
| `stacks.<name>.maintenance.schedule` | optional | the window for everything on that stack host |
| `backups.datastore_path` | required | |
| `backups.schedule` | optional | |
| `backups.retention` | optional | |
| `vaultwarden.admin_token` | Seed/recovery override only | removed from authored config after cutover |
| `vaultwarden.instance` | optional | |
| `media_storage.library` | required for media apps with a `library_subpath` | the library root as the containers see it; `app.library_subpath` hangs off it |
| `media_storage.mounts` | required for media apps with a `library_subpath` | list of `{host, path}` — storage the node already mounts, attached to the stack host as Proxmox mountpoints. Nothing is created, exported or formatted |
| `stacks.<name>.{cores,memory,disk_volume}` | optional | deep-merged over `vars/stack-defaults.yml`; applies when the stack host is CREATED |
| `stacks.<name>.pool` | optional | pool the stack host allocates from. A stack whose name matches a pool name inherits it without this key; a pool named here must exist on the network |
| `stacks.<name>.ip_address` | optional | pins the stack host to an exact address, honoured or refused with the conflict named |
| `stacks.<name>.boot_order` | optional | the stack host's Proxmox startup tier. A property of the stack, like its sizing — an app cannot set it, or the tier would depend on deploy order |
| `stacks.<name>.shared` | optional, default `false` | `true` means this stack host deliberately serves EVERY estate. See *Stack identity and estate isolation* below |

#### Stack identity and estate isolation

A **stack** is a bare concept — `media`, `sso`, `monitoring` — and never a hostname or a
tag. Both are derived from it, and both add the estate when the lab has more than one:

| | single estate (or no `domains:` map) | two or more estates |
|---|---|---|
| effective identity | `media` | `media-<estate>` |
| Proxmox hostname | `stack-media` | `stack-media-<estate>` |
| Proxmox tag | `_.stack+media` | `_.stack+media-<estate>` |
| inventory group | `lab_stack_media` | `lab_stack_media_<estate>` |

The suffix rule is the repository's existing instance-naming rule applied to a host: one
estate means no suffix; two or more means every estate-scoped identity carries its estate.

**Merge behaviour.** `shared` merges exactly like the rest of the stack block —
`vars/stack-defaults.yml` supplies the git-managed value and
`config/infrastructure.yml`'s `stacks:` map is deep-merged over it
(`combine(recursive=True)`). It is resolved once per placement, before the lookup that
decides whether a host already exists, because it decides *which* host the app is looking
for. No app-level key overrides it: a stack host is shared by definition, so one tenant
must not be able to move it out from under the others.

**What `shared: true` does.** The identity takes no estate suffix, so every estate
resolves to one host, and that host is tagged `_.shared`. Both halves come from this one
declaration. Nothing infers the tag after the fact from where applications happened to
land; an ordinary hosting unit hosts exactly one estate, and there is no path by which an
app that did not declare sharing reaches another estate's host.

`_.shared` means deliberate cross-estate hosting, not lab-wide application scope.
`scope: lab` does not imply the tag. A hosting substrate that accepts estate-scoped
applications from more than one estate must declare `shared: true`; a native guest that
hosts only its lab-scoped application does not.

#### Maintenance schedules

`maintenance.schedule` is one primitive, resolved through the same chain as everything
else: **global → estate → stack → app**. `always` means "disrupt whenever it is needed",
a window means "only then", and `never` means notify-only. There is no separate class or
mode enum, because a schedule already expresses all three.

A narrower layer **replaces** what it inherits, whole. Half-merging two windows produces
a third window nobody declared, and the operator would have no way to read what a guest
will actually do. This mirrors the same decision for estates in §3.

An app declares its own in `config/apps/<instance>.yml`:

```yaml
maintenance:
  schedule: never                # or always, or {days:, start:, duration:}
```

Several apps on one guest **intersect** rather than compete: a shared stack host may only
reboot inside every one of their windows, one app scheduled `never` holds the whole guest,
and two windows that never overlap are reported as a conflict rather than settled by
picking a winner. Container restarts stay finer-grained — Watchtower restarts one
container, so it follows that app's own schedule.

A resolved schedule is **enforced by the guest**, not by anything watching it. It becomes
a systemd `OnCalendar` in `homelab-maintenance.timer` on the guest, written at deploy time,
and the guest reboots itself when the window opens — and only if `/var/run/reboot-required`
is actually there. `never` removes the timer, which is what notify-only means. Nothing
polls: a job asking "is it time yet" on a fixed interval would re-implement the schedule on
top of itself and would miss any window that opened while the control plane was down.

`ansible/scripts/maintenance-schedule.py` owns the arithmetic,
`ansible/tasks/maintenance/resolve-schedule.yml` is the seam, and
`ansible/tasks/maintenance/install-guest-timer.yml` is the enforcement point. Consumers read
the resolved answer (`mode`, `due`, `text`, `cron`, `oncalendar`, `monitor_only`,
`next_open`, `conflict`) and never re-derive it.

### Runtime secrets and external unlock material

`lab-run.sh` constructs `homelabinfra_vault` in memory from canonical organization-owned
Vaultwarden items, then recursively overlays it on topology as `homelabinfra_infra`.
The canonical top-level items are:

| Item | Representative fields |
|---|---|
| `homelab-infra/proxmox` | `api_token_secret` |
| `homelab-infra/runner` | `ssh_private_key` |
| `homelab-infra/vaultwarden` | `admin_token` |
| `homelab-infra/notifications` | `password`, `token` |
| `homelab-infra/sso` | `token`, `admin_password`, `postgres_password`, `secret_key` — **the DEFAULT estate's SSO only**; an instance carrying `routing.estate` writes `homelab-infra/estates/<estate>/sso` instead |
| `homelab-infra/monitoring` | `api_key`, `admin_password` (the wiring needs `admin_password`, not the key) |
| `homelab-infra/metrics` | `admin_password` |
| `homelab-infra/backups` | `api_token_secret` |
| `homelab-infra/dns` | `api_key`, `api_secret` |
| `homelab-infra/mail` | `password` |
| `homelab-infra/reverse_proxy` | `dns_api_token` |
| `homelab-infra/media/<instance>` | `api_key`, `password`, or `arl` as applicable |
| `homelab-infra/apps/<instance>` | application-owned credentials. Database provisioning writes `database_provider`, `database_host`, `database_port`, `database_name`, `database_user`, and hidden `database_password` here; the backend never places an application password in generated facts. |
| `homelab-infra/estates/<estate>/<role>` | estate-scoped secret fields — e.g. `.../sso` (`token`, `admin_password`, `postgres_password`, `secret_key`) and `.../dns` (`api_token`). A non-default estate must not write a top-level role item or read the default estate's credential |

The following process variables are external control-plane inputs. Rundeck injects them
through per-job secure options backed by AES-GCM Key Storage; they are not ordinary job
options or config-file values:

| Env var | Overrides | Read by |
|---|---|---|
| `BW_CLIENTID` | automation-account API client ID | `bw login --apikey` |
| `BW_CLIENTSECRET` | automation-account API client secret | `bw login --apikey` |
| `BW_PASSWORD` | automation-account master password | `bw unlock --passwordenv` |
| `VAULTWARDEN_ADMIN_TOKEN` | server administration, enrollment and recovery | admin API only |
| `RUNDECK_API_TOKEN` | job import/cutover control-plane calls | selected maintenance jobs only |

Three more environment variables are Seed-mode inputs rather than control-plane material.
`CLOUDFLARE_API_TOKEN` supplies the ACME DNS-01 challenge credential
(`reverse_proxy.dns_challenge`), `LAB_DNS_API_KEY` / `LAB_DNS_API_SECRET` supply the
DNS provider's own RECORD-WIRING credential, and `LAB_MAIL_PASSWORD` supplies the SMTP
AUTH password — all three overlaid onto `homelabinfra_infra.{dns,mail}` by
`load-user-vars.yml`. They are different credentials for different jobs — one proves
domain ownership to a CA, one writes an A record, one authenticates outbound mail — and
conflating them points certificate issuance, or an app's mail settings, at the wrong
material.

`rundeck/bootstrap-rundeck.sh` asks for the record-wiring credential in the same breath as
`dns.provider` and writes `/etc/homelab-infra/secrets.d/dns.env`; `vaultwarden-cutover.yml`
imports it into `homelab-infra/dns`, after which the vault is the only source. Declaring
`dns.provider` without the credential is not a partial configuration — the wiring asserts
it, so the next app deploy fails.

In Seed mode, the Proxmox and administration environment variables remain temporary inputs. Once
`/etc/homelab-infra/state/vault-mode` exists, `lab-run.sh` will not source seed files even
if they are recreated. It obtains Proxmox and SSH material from Vaultwarden and cleans the
vault session and temporary key after the child playbook exits.

### The temporary Vaultwarden admin token sink (Seed mode)

`VAULTWARDEN_ADMIN_TOKEN` differs from the Proxmox token in one way that matters: **the
platform generates it rather than receiving it.** Vaultwarden has no admin token until its
first deploy creates one, so there is nothing for an operator to stage in advance.

`tasks/vaultwarden/token-sink.yml` gives that generated value a machine-readable home.
`roles/vaultwarden` writes it there on the run that generates it; `playbooks/bootstrap.yml`
reads it back two plays later and continues without stopping for a human.

| Order | Sink | Used when |
|---|---|---|
| 1 | `$VAULTWARDEN_TOKEN_SINK` | an operator points it somewhere deliberately |
| 2 | `/etc/homelab-infra/secrets.d/vaultwarden.env` | on a runner — the directory exists and is writable |
| 3 | `<config>/.generated/vaultwarden.env` | bare CLI runs, where there is no `/etc/homelab-infra` |

`secrets.d/` is owned by the job user at `0700` so the first Vaultwarden deploy can write
the generated token. The cutover job verifies it in the canonical vault item and preserves
an AES-GCM Key Storage recovery copy before removing the sink. Neither seed directory is a
normal runtime source after the marker.

Resolution order for the token's **value** is the same everywhere it is read
(`roles/vaultwarden`, the bootstrap gate): env var, then
`infrastructure.vaultwarden.admin_token`, then the sink. The config key remains an accepted
override for Seed and recovery compatibility.

The plaintext token is never printed. If the sink is unwritable, the role stops before
installing the generated token on Vaultwarden so the next run can safely generate another.

### `config/.backups/` and `config/apps/.backups/`

Every write into `config/` goes through `tasks/config/write-config-file.yml`, which copies
the current content to `<dir>/.backups/<file>.<YYYYmmddTHHMMSS>` before replacing it, emits
the unified diff into the job log, and prunes to the newest 20 per file. This is the
platform's whole config-history mechanism, and it is deliberately point-in-time rather than
per-commit-with-message: it answers "what did this look like before" and "get it back", and
does not answer "who changed this and why". `.backups/` is inside the gitignored `config/`
tree, so the runner's refresh cannot touch it, and PBS carries it off the host with the rest
of the guest.

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
    dns:                           # optional — which DNS provider serves THIS estate
      provider: opnsense           # pihole | adguard | opnsense | none
      host: 192.168.1.1            # non-secret half only; see below
```

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
the **Store Secret** job (`playbooks/maintenance/store-secret.yml`) — the cutover
importer is Seed-mode-only and `lab-run.sh` refuses Seed mode once the vault-mode
marker exists.

Apps choose an estate with `routing.estate` (default: the default estate). A
non-default estate's Authentik is just another app deploy with
`routing.estate: <name>` — its `sso` facts land under `estates.<name>` (§3).

The required/optional split for `config/.generated/facts.yml` follows the canonical shape in
Section 3. Bootstrap writes one role key per `write-generated-facts.yml` call (deep-merge, so
partial files are normal mid-bootstrap), and
each role key carries exactly the fields listed in Section 3 for that role. Consumers guard
with `is defined` on the keys they read — a role absent from the file means that baseline
service has not been bootstrapped yet. The `config/apps/<instance>.yml`
schema is settled in the App-level layering note below.

## App-level layering note

The per-app merge (`vars/app-defaults/<app>.yml` → `config/apps/<instance>.yml` → `app_config`) is a
**separate** per-play merge done in the app template, **not** part of `homelabinfra_config`. It is
described here for completeness but governed by its own precedence; do not conflate it with the
four-layer `homelabinfra_config` merge in Section 4.

**Instance-file schema.** `config/apps/<instance>.yml` is loaded whole by
filename. The filename is the instance name (`-e instance=<name>`) and identifies the
guest, application record, and provider objects. Routing uses `routing.subdomain` as
described below. Top-level keys mirror the `<app>_defaults` dict in
`vars/app-defaults/<app>.yml`: `proxmox:` (native LXC) **or** `stack:` (Docker apps — a scalar such
as `media`), `app:` (port, data_path, config_path, plus app-specific keys), optional `update:`
(`github_repo`, `binary_path` — native GitHub-release apps only), and `routing:` (`proxy`,
`access`, `identity`, plus optional `subdomain` and `estate`). `routing.proxy`
(`internal | external | none`) selects **which** reverse proxy serves the app in a two-proxy
topology; `none` means the app is not routed at all, which is how the reverse proxy itself
avoids routing itself. `routing.access` (`internal | public | authenticated`, default `internal`)
is a separate axis: it decides **who** may reach the app through that proxy. `internal` makes
`tasks/wiring/caddy.yml` add a `remote_ip` matcher restricting the route to
`reverse_proxy.internal_cidrs`; `public` emits the route with no source matcher, which on a
WAN-facing Caddy publishes the app to the internet. `authenticated` emits the same open route as
`public` and additionally **asserts that
the app is really gated**: `routing.identity` must be `forward_auth` or `oidc` and `sso.provider`
must not be `none`. An `authenticated` route whose identity mode is `catalog` (a launch tile) or
`none` (no object at all) is an app the operator believes is protected and which is in fact open
to the internet. Access and identity stay separate fields — access is the network path, identity
is where authentication happens — and this is the one point at which the two must agree.
Enforcement of all three classes is Caddy's: `tasks/wiring/nginx.yml` does not read the access
class at all, so an Nginx lab publishes every route with no source matcher.
`routing.proxy: external` does not widen access — exposure is only ever
`routing.access`. `routing.identity` is the identity-mode
enum `none | catalog | oidc | forward_auth` (default `catalog`): `none` skips Authentik
entirely, `catalog` creates an Application tile only, `oidc` creates an OAuth2 provider +
Application (client_id/secret handed back to the deploy as `authentik_oidc_client_id/_secret`
facts — not recorded in the registry), `forward_auth` creates a proxy provider enforced at the
reverse proxy. Enforcement is the reverse-proxy wiring's job: `tasks/wiring/caddy.yml` and
`tasks/wiring/nginx.yml` both read `wiring_identity_mode` alongside `sso.provider` +
`sso.host`, and emit the outpost handler chain (Caddy) or the Authentik `auth_request`
snippet (NPM) only for `forward_auth`; every other mode keeps the plain route it always
had. The boolean `routing.auth` is **superseded** by `routing.identity`
— nothing reads it. `routing.subdomain` is the hostname on the estate domain. **Every routed application
declares it in `vars/app-defaults/<app>.yml`**, and an instance file overrides it only to
publish that instance somewhere else. The fall-back when nothing declares it is the
instance name, and that fall-back is a trap rather than a feature: it couples the published
URL to the filename, so an estate-scoped instance would publish
`<app>-<estate>.<domain>`. Declaring the subdomain per application is what leaves the
instance name free to carry identity — filename, guest hostname, Proxmox tag — without the
URL following it around. `routing.estate` names a `domains:` estate (§5) and is authored per instance.
`app-defaults/` must not declare one and `rundeck/render-job.py` rejects it: that file is
git-managed and ships to every lab, so an estate named there is one the next clone has
never declared, and its config-doctor refuses the instance that inherits it.
Media-stack instances may add three optional `app:` keys read only by
`wire-media-stack.yml`: `media_kind` (the app kind — its presence is what enrols
the instance in media wiring), `host` (an explicit base URL, for an app this lab
did not deploy) and `api_key` (when the app's key cannot be read from
`config_path/config.xml`).
The whole file merges over `<app>_defaults` via `combine(recursive=True)`, later layer wins per key.
Because the merge is recursive, an override must match the default's shape: replacing a mapping (e.g.
`app:`) with a scalar clobbers the entire subtree, so instance files never restate a mapping key as a
bare scalar.

When `infrastructure.yml` declares two or more estates, an estate-scoped catalog application
must use `<app>-<estate>[-<variant>]` as its instance name and must author the same estate in
`routing.estate`. There is no unsuffixed default estate. Lab-scoped platform services keep
their ordinary instance names because one deployment serves the whole lab.
