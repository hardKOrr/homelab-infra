# Home Assistant role

This role deploys Home Assistant as one Docker Compose service on the application's own
cloud-init Debian VM with a dedicated Proxmox USB resource mapping for its Zigbee coordinator.

Integration credentials are read from the hidden `integration_credentials` JSON field in
`homelab-infra/apps/<instance>` and rendered to a root-only `secrets.yaml`. They are never
accepted in `config/apps/<instance>.yml` or printed by Ansible.
