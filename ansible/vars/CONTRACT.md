# Variable loading contract

This is the authoritative variable-loading contract for the `homelabinfra_*` namespaces and config
files.

## 1. The three namespaces

- `homelabinfra_config.*` — merged user + defaults, the **input** layer (available at provision time).
- `homelabinfra_instance.*` — facts **computed at runtime** (IP allocation, vmid, etc.).
- `homelabinfra_infra.*` — the **service registry**: generated provider topology recursively
  overlaid in memory with Vaultwarden runtime fields.

`homelabinfra_config.infrastructure` and `homelabinfra_infra` are **not the same dict**:
`infrastructure.yml` feeds both — its provider *choices* merge into `homelabinfra_config.infrastructure`
(input layer), and those choices plus bootstrap-written endpoints/tokens land in `homelabinfra_infra`
(the registry).

`homelabinfra_proxmox_inventory_complete` is a controller inventory boolean, outside
these three mappings. The supported inventory plugin resets it before parsing and sets
it true only after successful enumeration, with no offline or unknown nodes.
`homelabinfra_proxmox_inventory_offline_nodes` lists offline or unknown node names for
diagnostics. Guest lookup and allocation require
`homelabinfra_proxmox_inventory_complete: true`. Neither variable is user-authored
configuration, a cached fact or a service-registry field; see
[`Proxmox inventory safety`](../tasks/proxmox/README.md#inventory-availability-and-trust).

## 2. Load map: file → wrapper → target key

| File | Wrapper in file | Loaded into | Notes |
|---|---|---|---|
| `vars/homelabinfra-defaults.yml` | `homelabinfra_defaults:` | `homelabinfra_config` (seed, lowest precedence) | unwrapped before merge |
| `config/proxmox.yml` | none (top-level `proxmox:`, `networks:`, `ansible:`) | `homelabinfra_config` (loader injects those three keys) | optional at load time; callers validate required keys |
| `config/infrastructure.yml` | none; the whole file is the infrastructure shape | `homelabinfra_config.infrastructure` | optional at load time; callers validate required keys |
| `vars/app-defaults/<app>.yml` | none | `app_config` (per-play app merge — see [app layering](contract/app-layering.md)) | separate merge, not part of `homelabinfra_config` |
| `config/apps/<instance>.yml` | none | `app_config` (per-play app merge) | whole file merges over `<app>_defaults` via `combine(recursive=True)` — see [app layering](contract/app-layering.md) |
| `config/.generated/facts.yml` | none | generated base of `homelabinfra_infra`; Vaultwarden runtime fields overlay it recursively | written incrementally by `write-generated-facts.yml` |
| `user_vars_file` (back-compat) | `homelabinfra_config:` | `homelabinfra_config` | legacy single-file path; already self-wrapping |

The [app hosting contract](contract/hosting.md) declares which hosting forms each app
supports.

## 3. Canonical `homelabinfra_infra` shape

The registry is role-keyed and provider-agnostic. Consumers build derived values
(e.g. a notification URL) from `host` + `topic`; the registry never stores pre-built URLs.
Every HTTP-service `host` value is a full base URL **including scheme** (e.g.
`http://192.0.2.20`) — consumers concatenate paths onto it directly; consumers needing a
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
                 shared_storage_enabled, shared_storage_class, shared_storage_snapshot_class, shared_storage_target,
                 firewall_alias, failure_domain_mode, nodes, removal_in_progress }
media:                               # optional — app-to-app wiring for the media stack
  <instance>: { app, host, config_path, ... }   # credentials overlay from Vaultwarden
databases:                           # optional — independently deployed database backends
  <instance>: { provider, host, port, client_hosts } # host is a bare SSH/database address
apps:                               # optional — named application endpoints
  <instance>: { provider, instance, host, port, url, ssh_port } # topology only
runner:                              # the host this platform runs FROM — see below
  { provider, instance, host, vmid, node, checkout_path, venv_path, branch }
estates:                             # optional — only when infrastructure.yml declares domains:
  <estate-name>:                     # non-default estates only; the default estate uses
    sso: { provider, instance, host }          # the top-level keys above
    dns: { provider, host }                    # optional — global dns serves the estate if absent
    mail: { password }                         # optional scoped SMTP credential only
```

**`runner` — the host the platform runs from.** Written by
`rundeck/bootstrap-rundeck.sh`, not by any playbook, because the runner is created before
any playbook can run. It exists so playbooks and `status.yml` can name the host they are
executing on. `provider` is `rundeck`; `host` is the UI's base URL including
scheme; `checkout_path` and `venv_path` are the paths `lab-run.sh` resolves from
`/etc/homelab-infra/lab-run.env`; `branch` is the branch the checkout tracks and refreshes
to only when a caller explicitly requests it. Nothing wires against this key — it is
descriptive.

**Estate scoping.** `infrastructure.yml` may declare a `domains:` map of named
estates ([§5](contract/estates.md)); apps pick one with `routing.estate`. Estate-bound role keys (`sso`,
optionally `dns`) for a non-default estate are written under `estates.<name>` by
`write-generated-facts.yml` (`generated_facts_estate`) and overlaid onto the top-level
keys by `tasks/resolve-estate.yml` before wiring runs — replacement is whole-key, never
a recursive merge, so a default-estate token cannot leak into another estate's wiring.
Mail is the one shared role with an estate-visible identity: the global relay and
credential remain the fallback, while `domains.<estate>.mail` selects the effective
From identity and may override non-secret relay fields. A separate relay login, when
required, is stored as `homelab-infra/estates/<estate>/mail` and is selected only for
that estate. All other role keys (reverse_proxy, notifications, monitoring, metrics,
backups, vaultwarden) are global: one instance serves every estate. The default estate
(and every lab without a `domains:` map) reads and writes only the top-level keys —
unchanged behavior.

When a single-estate lab adds a second estate, existing estate-scoped instance filenames
can remain unchanged if their exact names are listed in the default estate's optional
`legacy_instances` list. Those names stay assigned to the default estate; every new
estate-scoped instance must use `<app>-<estate>[-<variant>]`. The list is validated against
the catalog and existing `config/apps/<instance>.yml` files so it cannot silently assign a
lab-scoped app, a missing file, or a non-default instance.

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
| `firewall` | `api_key`, `api_secret` | opnsense | OPNsense API key/secret for an account holding only the `Firewall: Rules [new]`, `Firewall: NAT: Destination NAT`, and `Firewall: Alias: Edit` privileges; a separate account from the Unbound-only DNS key |
| `mail` | `password` | smtp | Shared SMTP AUTH password for `mail.username`; Vaultwarden-only, never authored |
| `estates.<estate>.mail` | `password` | smtp | Optional estate-specific SMTP AUTH password; selected only for that estate, never authored |

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
| `firewall_alias` | the OPNsense host alias containing every node address; present when `firewall.provider` is `opnsense` |
| `ingress_controller` | in-cluster controller terminating plain HTTP on the VIP (`traefik`) |
| `storage_class` | default StorageClass. The current `homelab-local-path` class is node-pinned: a pod whose volume lives on an unavailable node stays Pending rather than rescheduling |
| `storage_reclaim_policy` | `Retain` — only the removal job's explicit `delete_data` removes a volume |
| `storage_path` | the directory on each node that every volume lives under. Topology, not a secret. `maintenance/reclaim-volume.yml` refuses to delete a directory that is not under it, so the guard is inert while this is absent |
| `shared_storage_enabled` | whether the opt-in NFS CSI contract was deployed; `false` preserves the existing node-local-only behavior |
| `shared_storage_class` | non-default `nfs.csi.k8s.io` StorageClass. Empty when shared storage is disabled; workloads must name it explicitly |
| `shared_storage_snapshot_class` | retained NFS CSI `VolumeSnapshotClass`. Empty when shared storage is disabled |
| `shared_storage_target` | non-secret target accountability declaration: name, server, share, capacity owner and failure domains. It does not prove target health or capacity |
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

**`apps` — named application endpoints.** This optional registry is for app-to-app
dependencies, not platform wiring. Forgejo publishes its HTTP endpoint and direct Git
SSH port here; Forgejo Runner resolves the named Forgejo instance from this map before
deploying. It contains topology only — no registration token, admin token, password,
or other credential-shaped field. Removing an app prunes only its exact key and never
removes the independently managed backend it consumed.

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

The global `infrastructure.mail` block is the default estate's complete relay and identity.
A `domains.<estate>.mail` block is optional and non-secret: its `from_address` (required
when the block enables SMTP) and optional `from_name`, `provider`, `host`, `port`,
`encryption`, and `username` overlay the global block. The resolver assigns the resulting
complete block as one whole `homelabinfra_infra.mail` key, so a public estate cannot keep
the default estate's From-domain through a partial recursive fact merge. An estate with no
`mail` block is a deliberate no-op and inherits the global block byte-for-byte.

`mail.username` is the non-secret half of SMTP AUTH (often the same value as
`from_address`). The shared password lives only in `homelab-infra/mail`; if a relay requires
an estate-specific login, only that estate's password lives in
`homelab-infra/estates/<estate>/mail`, supplied after cutover with Store Secret. The
selected estate item is never used for another estate, and an absent scoped item falls
back to the shared global relay credential. Neither password appears in tracked config or
generated facts. Like `notifications`, mail has no per-record external resource to create,
so there is no `tasks/wiring/<provider>.yml` pair — an app that sends mail includes the
shared `ansible/tasks/mail/resolve-mail.yml`, which resolves its estate and reads
`homelabinfra_infra.mail` to set one `wiring_mail` fact (`enabled, provider, host, port,
encryption, from_address, from_name, username, password`) with `no_log: true`.
`wiring_mail.enabled` is `false` whenever `mail.provider` is `none` or absent, and every
consumer must check it before writing its own SMTP settings — `resolve-mail.yml` does not
configure any application itself.

## 4. Merge order (low → high precedence)

1. `vars/homelabinfra-defaults.yml` (unwrap `homelabinfra_defaults:`) → seed of `homelabinfra_config`.
2. `config/proxmox.yml` (loader injects `proxmox`/`networks`/`ansible` under `homelabinfra_config`).
3. `config/infrastructure.yml` (loader injects under `homelabinfra_config.infrastructure`).
4. `user_vars_file` if present (back-compat; already carries its own `homelabinfra_config:` wrapper).

All merges use `combine(recursive=True)`; later layers win per key.

## 5. Required vs optional keys per config file

Each config file's keys, and the contracts that sit beside them, live in `contract/`:

- [`config/proxmox.yml`](contract/proxmox.md), including database backend instance placement
- [`config/infrastructure.yml`](contract/infrastructure.md), including stack identity and
  estate isolation and maintenance schedules
- [Estates, public DNS and per-estate DNS](contract/estates.md): the `domains:` map
- [Recovery and application state](contract/recovery.md): PBS datastore recovery mode, the
  `recovery:` contract, application database provisioning and LiteLLM external state
- [Secrets and config history](contract/secrets.md): runtime secrets, the Seed-mode
  Vaultwarden admin token sink and `config/.backups/`
- [App-level layering](contract/app-layering.md): `config/apps/<instance>.yml` over
  `vars/app-defaults/<app>.yml`
- [App hosting contract](contract/hosting.md): `hosting.method`, `supported` and `impossible`

The required/optional split for `config/.generated/facts.yml` follows the canonical shape in
Section 3. Bootstrap writes one role key per `write-generated-facts.yml` call (deep-merge, so
partial files are normal mid-bootstrap), and
each role key carries exactly the fields listed in Section 3 for that role. Consumers guard
with `is defined` on the keys they read — a role absent from the file means that baseline
service has not been bootstrapped yet. The `config/apps/<instance>.yml`
schema is settled in [App-level layering](contract/app-layering.md).
