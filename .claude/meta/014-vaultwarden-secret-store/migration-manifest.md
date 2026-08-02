# Live migration manifest

This is the non-secret, field-name-only manifest derived from every producer and consumer
in the repository. It is not yet the exact live manifest: the workspace has no live runner
`config/`, and no remote read was authorized. Before cutover, inventory the live files and
reduce this list to fields that actually exist; do not retrieve values into a log.

| Source before cutover | Canonical item | Candidate fields |
|---|---|---|
| temporary Proxmox seed | `homelab-infra/proxmox` | `api_token_secret` |
| runner SSH identity | `homelab-infra/runner` | `ssh_private_key` |
| temporary admin-token sink | `homelab-infra/vaultwarden` | `admin_token` |
| generated/authored notifications | `homelab-infra/notifications` | `password`, `token`, `webhook_url` |
| generated Authentik | `homelab-infra/sso` | `token`, `admin_password`, `postgres_password`, `secret_key` |
| generated Uptime Kuma | `homelab-infra/monitoring` | `token`, `admin_password` |
| generated Grafana | `homelab-infra/metrics` | `admin_password` |
| generated PBS | `homelab-infra/backups` | `api_token_secret` |
| authored DNS provider | `homelab-infra/dns` | `api_key`, `api_secret`, `token` |
| generated/media config | `homelab-infra/media/<instance>` | `api_key`, `password`, `arl` |
| authored app config | `homelab-infra/apps/<instance>` | every detected secret-shaped leaf |
| authored estate DNS | `homelab-infra/estates/<estate>/dns` | `api_token`, `api_key`, `api_secret` |
| Authentik OIDC wiring | `homelab-infra/apps/<instance>` | `oidc_client_id`, `oidc_client_secret` |

External AES-GCM Key Storage entries are not migrated into the item vault as unlock
credentials:

- `vaultwarden-machine/client-id`
- `vaultwarden-machine/client-secret`
- `vaultwarden-machine/master-password`
- `vaultwarden-machine/admin-token`
- `rundeck/api-token`

The first three unlock the automation vault. The admin token controls the Vaultwarden
server. The Rundeck token controls job import and cutover API calls. The converter password
is separately backed up and is never a Key Storage or Vaultwarden item.

## Approval boundary

The inventory pass may list file paths and secret field names only. It must not print
values. Cutover, deletion of seed files, Key Storage deletion, credential rotation, or
marker creation requires a second explicit approval after the exact live list is reviewed.
