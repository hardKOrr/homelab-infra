# Variable loading contract

This is the authoritative variable-loading contract for the `homelabinfra_*` namespaces and config
files — the single data-shape reference downstream slices cite. Inspection rules that protect these
shapes: `.claude/specs/config-layering.md` and `.claude/specs/namespace-merge-discipline.md`.

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
# config/.generated/facts.yml, loaded whole into homelabinfra_infra
domain: homelab.example.com          # copied from infrastructure.yml
reverse_proxy: { provider, instance, host, port }
sso:           { provider, instance, host, token }
notifications: { provider, instance, host, topic }   # NOT ntfy_url — consumers build {{ host }}/{{ topic }}
monitoring:    { provider, instance, host, token, notification_id }
dns:           { provider, host, api_key }
backups:       { instance, host, datastore, datastore_path }
vaultwarden:   { host, port }        # populated after bootstrap step 1
```

Provider-specific optional fields, written by the app slice that deploys the provider and
read only by that provider's wiring tasks (slices 301–305):

| Role key | Field | Provider | Purpose |
|---|---|---|---|
| `reverse_proxy` | `token` | nginx | pre-issued NPM JWT; skips the login round-trip |
| `reverse_proxy` | `admin_user`, `admin_password` | nginx | NPM admin login, exchanged for a JWT per run |
| `reverse_proxy` | `letsencrypt_email` | nginx | when set, new proxy hosts request a LE certificate and force SSL; omit for internal-only labs |
| `monitoring` | `notification_id` | uptime_kuma | id of the Ntfy notification channel attached to every monitor |
| `dns` | `api_secret` | opnsense | second half of the OPNsense API key/secret basic-auth pair |
| `dns` | `api_key` | pihole | the Pi-hole app password, exchanged for a session SID (v6+ only) |
| `dns` | `validate_certs` | opnsense, pihole | default `false` — lab DNS hosts serve self-signed certificates |

`monitoring` is the Shape B role key for uptime monitoring; the provider-named
`uptime_kuma` key that app playbooks briefly gated on is superseded — do not use it.
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
| `proxmox.api_token_secret` | required | secret |
| `networks.<name>.cidr` | required | per named subnet |
| `networks.<name>.gateway` | required | per named subnet |
| `networks.<name>.dns_servers` | required | per named subnet |
| `networks.<name>.bridge` | required | per named subnet |
| `networks.<name>.vlan` | optional | |
| `networks.<name>.ip_offset` | optional | |
| `networks.<name>.max_hosts` | optional | |
| `ansible.ssh_user` | required | |
| `ansible.ssh_public_key` | required | |

### `config/infrastructure.yml`

| Key | Required? | Default / notes |
|---|---|---|
| `domain` | required | |
| `reverse_proxy.provider` | required | `caddy \| nginx \| none` |
| `reverse_proxy.instance` | required unless provider `none` | |
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
| `backups.datastore_path` | required | |
| `backups.schedule` | optional | |
| `backups.retention` | optional | |
| `vaultwarden.admin_token` | required | secret; written after bootstrap step 1 |
| `vaultwarden.instance` | optional | |

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
(`github_repo`, `binary_path` — native GitHub-release apps only), and `routing:` (`proxy`, `auth`).
The whole file merges over `<app>_defaults` via `combine(recursive=True)`, later layer wins per key.
Because the merge is recursive, an override must match the default's shape: replacing a mapping (e.g.
`app:`) with a scalar clobbers the entire subtree, so instance files never restate a mapping key as a
bare scalar.
