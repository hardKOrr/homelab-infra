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
| `config/apps/<instance>.yml` | none | `app_config` (per-play app merge) | schema contradictory today → slice 005 |
| `config/.generated/facts.yml` | none | `homelabinfra_infra` (whole file, via `include_vars … name: homelabinfra_infra`) | written by `write-generated-facts.yml` (TODO stub → slice 200) |
| `user_vars_file` (back-compat) | `homelabinfra_config:` | `homelabinfra_config` | legacy single-file path; already self-wrapping |

## 3. Canonical `homelabinfra_infra` shape

There is exactly one shape — role-keyed, provider-agnostic (Shape B). Consumers build derived values
(e.g. a notification URL) from `host` + `topic`; the registry never stores pre-built URLs.

```yaml
# config/.generated/facts.yml, loaded whole into homelabinfra_infra
domain: homelab.example.com          # copied from infrastructure.yml
reverse_proxy: { provider, instance, host, port }
sso:           { provider, instance, host, token }
notifications: { provider, instance, host, topic }   # NOT ntfy_url — consumers build {{ host }}/{{ topic }}
dns:           { provider, host, api_key }
backups:       { instance, datastore_path }
vaultwarden:   { host, port }        # populated after bootstrap step 1
```

Superseded — do not use: (a) Shape-A flat pre-built URL `notifications.ntfy_url` +
`.notifications.topic` (read today by `check-native-updates.yml`, `restart-app.yml`,
`guest-bootstrap.yml`) — reconciled by slice 200; (b) the service/function-keyed stub sketch in
`write-generated-facts.yml`'s header comment (`vaultwarden:{url,admin_token}`, `caddy:{admin_api_url}`,
…) — superseded by slice 200.

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
Section 3 but its authoritative required-key list is owned by **slice 200** (it defines what
bootstrap writes); the `config/apps/<instance>.yml` schema is owned by **slice 005**. Contract names
them here, does not resolve them.

## 6. Known conflicts and owning slices

| Conflict | Contract's canonical decision | Resolving slice |
|---|---|---|
| `config.example/*.yml` unwrapped top-level keys vs namespaces the code reads | loader injects namespaces (001); examples reconciled to match (002) | **001 + 002** |
| `notifications.ntfy_url` (Shape-A leak) vs `notifications.host` + `.topic` | registry stores `host` + `topic`; consumers build the URL; three consumers flagged for alignment | **200** |
| `write-generated-facts.yml` stub service-keyed sketch vs canonical Shape B | Shape B supersedes the stub sketch | **200** |
| `config/apps/<instance>.yml` schema contradictory across repo | named, not resolved here | **005** |
| `networks:` null subtree in `homelabinfra-defaults.yml` (config-layering violation) | remove null subtree (use `{}` or omit) | **002** |

## App-level layering note

The per-app merge (`vars/app-defaults/<app>.yml` → `config/apps/<instance>.yml` → `app_config`) is a
**separate** per-play merge done in the app template, **not** part of `homelabinfra_config`. It is
described here for completeness but governed by its own precedence; do not conflate it with the
four-layer `homelabinfra_config` merge in Section 4.
