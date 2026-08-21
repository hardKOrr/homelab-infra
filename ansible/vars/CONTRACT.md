# Variable loading contract

This is the authoritative variable-loading contract for the `homelabinfra_*` namespaces and config
files — the single data-shape reference downstream slices cite. Inspection rules that protect these
shapes: `docs/specs/config-layering.md` and `docs/specs/namespace-merge-discipline.md`.

## 1. The three namespaces

- `homelabinfra_config.*` — merged user + defaults, the **input** layer (available at provision time).
- `homelabinfra_instance.*` — facts **computed at runtime** (IP allocation, vmid, etc.).
- `homelabinfra_infra.*` — the **service registry**: provider choices + endpoints + tokens, loaded
  from `config/.generated/facts.yml` (read at wiring time).

`homelabinfra_config.infrastructure` and `homelabinfra_infra` are **not the same dict**:
`infrastructure.yml` feeds both — its provider *choices* merge into `homelabinfra_config.infrastructure`
(input layer), and those choices plus bootstrap-written endpoints/tokens land in `homelabinfra_infra`
(the registry).

## 2. Load map: file → wrapper → target key

| File | Wrapper in file | Loaded into | Notes |
|---|---|---|---|
| `vars/homelabinfra-defaults.yml` | `homelabinfra_defaults:` | `homelabinfra_config` (seed, lowest precedence) | unwrapped before merge |
| `config/proxmox.yml` | none (top-level `proxmox:`, `networks:`, `ansible:`) | `homelabinfra_config` (loader injects those three keys) | **not yet wired in loader → slice 001** |
| `config/infrastructure.yml` | none (top-level `domain:`, `reverse_proxy:`, `sso:`, `notifications:`, `dns:`, `backups:`, `vaultwarden:`) | `homelabinfra_config.infrastructure` | **not yet wired in loader → slice 001** |
| `vars/app-defaults/<app>.yml` | none | `app_config` (per-play app merge — see app-layering note) | separate merge, not part of `homelabinfra_config` |
| `config/apps/<instance>.yml` | none | `app_config` (per-play app merge) | whole file merges over `<app>_defaults` via `combine(recursive=True)` — see app-layering note |
| `config/.generated/facts.yml` | none | `homelabinfra_infra` (whole file, via `include_vars … name: homelabinfra_infra`) | written incrementally by `write-generated-facts.yml` (slice 200) |
| `user_vars_file` (back-compat) | `homelabinfra_config:` | `homelabinfra_config` | legacy single-file path; already self-wrapping |

## 3. Canonical `homelabinfra_infra` shape

There is exactly one shape — role-keyed, provider-agnostic (Shape B). Consumers build derived values
(e.g. a notification URL) from `host` + `topic`; the registry never stores pre-built URLs.
Every `host` value is a full base URL **including scheme** (e.g. `http://192.168.1.20`) —
consumers concatenate paths onto it directly; consumers needing a bare hostname (e.g. a
shoutrrr URL) strip the scheme themselves.

```yaml
# config/.generated/facts.yml topology, overlaid in memory with Vaultwarden fields
domain: homelab.example.com          # copied from infrastructure.yml
reverse_proxy: { provider, instance, host, port, internal_cidrs }
sso:           { provider, instance, host, admin_user }
notifications: { provider, instance, host, topic }   # NOT ntfy_url — consumers build {{ host }}/{{ topic }}
monitoring:    { provider, instance, host, admin_user, notification_id }
metrics:       { provider, instance, host, prometheus_host, admin_user }
dns:           { provider, host }
backups:       { instance, host, datastore, datastore_path, api_token_id }
vaultwarden:   { host, port }
kubernetes:    { instance, provider, version, host, ingress_vip, ingress_controller,
                 storage_class, storage_reclaim_policy, storage_path,
                 failure_domain_mode, nodes }
media:                               # optional — app-to-app wiring for the media stack
  <instance>: { app, host, config_path, ... }   # credentials overlay from Vaultwarden
runner:                              # the host this platform runs FROM — see below
  { provider, instance, host, vmid, node, checkout_path, venv_path, branch }
estates:                             # optional — only when infrastructure.yml declares domains:
  <estate-name>:                     # non-default estates only; the default estate uses
    sso: { provider, instance, host }          # the top-level keys above
    dns: { provider, host }                    # optional — global dns serves the estate if absent
```

**`runner` — the host the platform runs from (slice 010).** Written by
`rundeck/bootstrap-rundeck.sh`, not by any playbook, because the runner is created before
any playbook can run. It exists so playbooks and `status.yml` can name the host they are
executing on. `provider` is `rundeck` or `semaphore`; `host` is the UI's base URL including
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

Provider-specific optional fields, written by the app slice that deploys the provider and
read only by that provider's wiring tasks (slices 301–305):

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

`monitoring` is the Shape B role key for uptime monitoring; the provider-named
`uptime_kuma` key that app playbooks briefly gated on is superseded — do not use it.

**`kubernetes` — the cluster hosting backend (slice 204).** Written by
`playbooks/apps/k3s-cluster.yml`. Topology only: the cluster's credentials — the etcd join
token and the administrative kubeconfig — are canonical in the organization-owned
Vaultwarden item `homelab-infra/<instance>` and never appear here.

| Field | Notes |
|---|---|
| `host` | Kubernetes API base URL including scheme, addressed at the founding node |
| `ingress_vip` | the one stable internal ingress address; what the platform Caddy proxies to |
| `ingress_controller` | in-cluster controller terminating plain HTTP on the VIP (`traefik`) |
| `storage_class` | default StorageClass. `local-path` is **node-pinned**: a pod whose volume lives on an unavailable node stays Pending rather than rescheduling |
| `storage_reclaim_policy` | `Retain` — only the removal job's explicit `delete_data` removes a volume |
| `storage_path` | the directory on each node that every volume lives under. Topology, not a secret. `maintenance/reclaim-volume.yml` refuses to delete a directory that is not under it, so the guard is inert while this is absent |
| `failure_domain_mode` | `distinct-nodes` when every server sits on a different Proxmox node, `single` otherwise. Consumers describing availability read this rather than counting nodes |
| `nodes` | the node declarations verbatim: name, Proxmox placement, address, sizing, taints |

**`media` — the app-to-app wiring registry (slice 504).** Unlike every other role
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

Ntfy ships closed (`auth-default-access: deny-all`, slice 401), so every notification
consumer must authenticate. Consumers treat the credential fields as **optional**: an
absent `token` means an Ntfy that predates slice 401 and still accepts anonymous
publishes, so they fall back to an unauthenticated POST rather than failing.
`dns.host` is the one `host` that may be a bare IP (`config.example/infrastructure.yml`
documents it that way for external, non-inventory hosts); the DNS wiring tasks prepend a
scheme when it is missing.

Superseded — do not use: (a) Shape-A flat pre-built URL `notifications.ntfy_url` +
`.notifications.topic` — all former readers (`check-native-updates.yml`, `restart-app.yml`,
`configure-unattended-upgrades.yml`) were reconciled to `host` + `topic` by slice 200;
(b) the service/function-keyed sketch that used to live in `write-generated-facts.yml`'s
header comment (`vaultwarden:{url,admin_token}`, `caddy:{admin_api_url}`, …) — replaced by
the Shape B implementation (slice 200).

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
| `proxmox.api_token_secret` | required *in file or env* | secret — **preferred shape is to omit it here** and supply `PROXMOX_API_TOKEN` in the environment (slice 010) |
| `proxmox.validate_certs` | optional | default `false` — a stock Proxmox node is self-signed, so guest creation fails with `CERTIFICATE_VERIFY_FAILED` when this verifies. `inventory/proxmox.yml` assumes the same. Set `true` once the node serves a trusted certificate |
| `proxmox.nodes` | optional | `{node_name: address}` for every cluster node, written by `bootstrap-rundeck.sh` from `pvesh get /cluster/status`. Consumed by `tasks/proxmox/register-nodes.yml`, which makes `delegate_to: <node name>` resolvable — node names have no `ansible_host` from the dynamic inventory. Falls back to `proxmox.api_host` for the targeted node when absent |
| `proxmox.storage` | required *in practice* | lab-wide storage for every guest; written by `bootstrap-rundeck.sh` from the first active storage advertising content type `rootdir`. Falls back to `local`, which a stock node **cannot** hold a container on. No `vars/app-defaults/*` pins storage — it is a node fact, not an app fact. Per-app override: `proxmox.disk_volume.storage` (LXC), `proxmox.vm.storage` (VM) |
| `networks.<name>.cidr` | required | per named subnet |
| `networks.<name>.gateway` | required | per named subnet |
| `networks.<name>.dns_servers` | required | per named subnet |
| `networks.<name>.bridge` | required | per named subnet |
| `networks.<name>.vlan` | optional | Proxmox VLAN tag; `0` or absent means untagged. One network per VLAN — the tag, subnet and gateway travel together, so an app changes VLAN by changing its `proxmox.network` name |
| `networks.<name>.reserved` | optional | list of spans never allocated: an address, `"a-b"`, or a CIDR. Independent of Proxmox — this is how a NAS, a switch or a router's DHCP range stays unallocatable |
| `networks.<name>.pools` | optional | `{<pool>: {range: "a-b"}}` or `{<pool>: {cidr: "..."}}`; each pool must sit inside `cidr`. A guest allocated into a pool never lands outside it, and an exhausted pool fails the run rather than spilling into the wider subnet |
| `networks.<name>.default_pool` | optional | pool used by a guest that names none and inherits none |
| `networks.<name>.ip_offset` | optional | fallback walk only (no pool applies). An index into the host range, not a last octet: at a /20, `10` is `x.0.10` and the range runs to `x.15.254` |
| `networks.<name>.max_hosts` | optional | fallback walk only: how far past `ip_offset` allocation may walk |

Pool selection, highest first: `proxmox.pool` in the instance file (must exist, or the
run fails); the pool named after the app's `stack`, used only if the network defines it;
`default_pool`; otherwise the `ip_offset`/`max_hosts` walk. `proxmox.ip_address` in an
instance file pins an exact address — honoured as written or refused with the conflict
named, never quietly replaced. `ansible/scripts/allocate-ip.py` decides; every refusal
carries its reason.
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
| `maintenance.boot.order` | optional | default Proxmox startup tier for a guest that declares no `proxmox.boot_order`; `50`. `none` disables boot ordering lab-wide and leaves `startup` unset on every guest |
| `maintenance.boot.up` | optional | seconds Proxmox waits after a guest before starting the next tier; `15` |
| `maintenance.schedule` | optional | when the lab may be DISRUPTED — `always`, `never`, or a window `{days, start, duration}`. Defaults to nightly 04:00 for 120 minutes. Update cadence is unaffected; only reboots and container restarts wait for it. `never` is notify-only |
| `domains.<estate>.maintenance.schedule` | optional | that estate's own window. Estates are independent clocks — a schedule is never merged or inherited ACROSS estates |
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

#### Maintenance schedules (slice 205)

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

### Runtime secrets and external unlock material (slice 014)

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
| `homelab-infra/reverse_proxy` | `dns_api_token` |
| `homelab-infra/media/<instance>` | `api_key`, `password`, or `arl` as applicable |
| `homelab-infra/apps/<instance>` | instance-specific secret fields |
| `homelab-infra/estates/<estate>/<role>` | estate-scoped secret fields — e.g. `.../sso` (`token`, `admin_password`, `postgres_password`, `secret_key`) and `.../dns` (`api_token`). A role that writes a top-level item unconditionally clobbers the default estate's copy the first time an estate deploys, and hands the new instance the default estate's credential through its own continuity read; both happened live on 2026-08-15 |

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

In Seed mode, the older Proxmox/admin environment variables remain temporary inputs. Once
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
override so a lab that pasted a token before this slice is unaffected by a `git pull`.

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

**`domains:` — named estates (optional).** An estate is a domain scope with its own
SSO instance (and optionally DNS and ACME DNS-challenge token), sharing the rest of
the platform. The plain `domain:` scalar stays valid as shorthand for one default
estate, so existing labs are untouched.

```yaml
domains:
  personal:
    domain: homelab.example.com
    default: true                  # exactly one entry; else the first entry is default
  foxglove:
    domain: foxglove.example.com
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

Until 2026-08-15 both mapped from one `estates/<name>/dns` item, which meant an
OPNsense key stored for record wiring arrived in the estate's `dns_challenge` block and
the caddy role would have issued that estate's certificates against `provider:
opnsense`.

A secret authored after the one-time Vaultwarden Cutover reaches either item through
the **Store Secret** job (`playbooks/maintenance/store-secret.yml`) — the cutover
importer is Seed-mode-only and `lab-run.sh` refuses Seed mode once the vault-mode
marker exists.

Apps choose an estate with `routing.estate` (default: the default estate). A
non-default estate's Authentik is just another app deploy with
`routing.estate: <name>` — its `sso` facts land under `estates.<name>` (§3).

The required/optional split for `config/.generated/facts.yml` follows the canonical shape in
Section 3. Slice 200 settled the write mechanics: bootstrap writes one role key per
`write-generated-facts.yml` call (deep-merge, so partial files are normal mid-bootstrap), and
each role key carries exactly the fields listed in Section 3 for that role. Consumers guard
with `is defined` on the keys they read — a role absent from the file means that baseline
service has not been bootstrapped yet. The `config/apps/<instance>.yml`
schema is settled in the App-level layering note below.

## 6. Known conflicts and owning slices

| Conflict | Contract's canonical decision | Resolving slice |
|---|---|---|
| `config.example/*.yml` unwrapped top-level keys vs namespaces the code reads | loader injects namespaces (001); examples reconciled to match (002) | **001 + 002** |
| `notifications.ntfy_url` (Shape-A leak) vs `notifications.host` + `.topic` | registry stores `host` + `topic`; consumers build the URL; all three consumers aligned | **200 (resolved)** |
| `write-generated-facts.yml` stub service-keyed sketch vs canonical Shape B | Shape B supersedes the stub sketch; implemented | **200 (resolved)** |
| `config/apps/<instance>.yml` schema across the repo | settled: filename = instance name; top-level keys mirror `<app>_defaults`; whole file merges via `combine(recursive=True)` — see App-level layering note | **005 (settled)** |
| `networks:` null subtree in `homelabinfra-defaults.yml` (config-layering violation) | remove null subtree (use `{}` or omit) | **002** |

## App-level layering note

The per-app merge (`vars/app-defaults/<app>.yml` → `config/apps/<instance>.yml` → `app_config`) is a
**separate** per-play merge done in the app template, **not** part of `homelabinfra_config`. It is
described here for completeness but governed by its own precedence; do not conflate it with the
four-layer `homelabinfra_config` merge in Section 4.

**Instance-file schema (settled by slice 005).** `config/apps/<instance>.yml` is loaded whole by
filename — the filename *is* the instance name (`-e instance=<name>`) and becomes the hostname,
Caddy subdomain, and Authentik app name. Its top-level keys mirror the `<app>_defaults` dict in
`vars/app-defaults/<app>.yml`: `proxmox:` (native LXC) **or** `stack:` (Docker apps — a scalar such
as `media_stack`), `app:` (port, data_path, config_path, plus app-specific keys), optional `update:`
(`github_repo`, `binary_path` — native GitHub-release apps only), and `routing:` (`proxy`,
`access`, `identity`, plus optional `subdomain` and `estate`). `routing.proxy`
(`internal | external | none`) selects **which** reverse proxy serves the app in a two-proxy
topology; `none` means the app is not routed at all, which is how the reverse proxy itself
avoids routing itself. `routing.access` (`internal | public | authenticated`, default `internal`)
is a separate axis: it decides **who** may reach the app through that proxy. `internal` makes
`tasks/wiring/caddy.yml` add a `remote_ip` matcher restricting the route to
`reverse_proxy.internal_cidrs`; `public` emits the route with no source matcher, which on a
WAN-facing Caddy publishes the app to the internet. `authenticated` (added 2026-08-18 by the
Kubernetes hosting backend) emits the same open route as `public` and additionally **asserts that
the app is really gated**: `routing.identity` must be `forward_auth` or `oidc` and `sso.provider`
must not be `none`. An `authenticated` route whose identity mode is `catalog` (a launch tile) or
`none` (no object at all) is an app the operator believes is protected and which is in fact open
to the internet. Access and identity stay separate fields — access is the network path, identity
is where authentication happens — and this is the one point at which the two must agree.
Enforcement of all three classes is Caddy's: `tasks/wiring/nginx.yml` does not read the access
class at all, so an Nginx lab publishes every route with no source matcher. The proxy/access split
dates from 2026-08-02, and `routing.proxy: external` no longer widens access — exposure is only
ever `routing.access`. `routing.identity` is the identity-mode
enum `none | catalog | oidc | forward_auth` (default `catalog`): `none` skips Authentik
entirely, `catalog` creates an Application tile only, `oidc` creates an OAuth2 provider +
Application (client_id/secret handed back to the deploy as `authentik_oidc_client_id/_secret`
facts — not recorded in the registry), `forward_auth` creates a proxy provider enforced at the
reverse proxy. Enforcement is the reverse-proxy wiring's job: `tasks/wiring/caddy.yml` and
`tasks/wiring/nginx.yml` both read `wiring_identity_mode` alongside `sso.provider` +
`sso.host`, and emit the outpost handler chain (Caddy) or the Authentik `auth_request`
snippet (NPM) only for `forward_auth`; every other mode keeps the plain route it always
had. The boolean `routing.auth` is **superseded** by `routing.identity`
— nothing reads it. `routing.subdomain` overrides the hostname on the estate domain (default:
the instance name); `routing.estate` names a `domains:` estate (§5).
Media-stack instances may add three optional `app:` keys read only by
`wire-media-stack.yml`: `media_kind` (the app kind — its presence is what enrols
the instance in media wiring), `host` (an explicit base URL, for an app this lab
did not deploy) and `api_key` (when the app's key cannot be read from
`config_path/config.xml`).
The whole file merges over `<app>_defaults` via `combine(recursive=True)`, later layer wins per key.
Because the merge is recursive, an override must match the default's shape: replacing a mapping (e.g.
`app:`) with a scalar clobbers the entire subtree, so instance files never restate a mapping key as a
bare scalar.
