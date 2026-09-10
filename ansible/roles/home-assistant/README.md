# Home Assistant role

This role deploys Home Assistant as one Docker Compose service on the application's own
cloud-init Debian VM. The VM and Docker layers are deliberately ordinary; the reason this
application is not an LXC is the dedicated Proxmox USB resource mapping for its Zigbee
coordinator.

Integration credentials are read from the hidden `integration_credentials` JSON field in
`homelab-infra/apps/<instance>` and rendered to a root-only `secrets.yaml`. They are never
accepted in `config/apps/<instance>.yml` or printed by Ansible. See
[`docs/specs/home-assistant-recovery.md`](../../../docs/specs/home-assistant-recovery.md)
for backup, restore, and USB detach procedure.
