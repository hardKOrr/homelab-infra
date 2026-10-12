# Home Assistant role

This role deploys Home Assistant as one Docker Compose service on the application's own
cloud-init Debian VM. USB passthrough is optional; an empty `app.usb_mapping` omits both the
Proxmox attachment and the Compose device binding. When configured, the named Proxmox USB
mapping must be declared dedicated and the guest device path must name its serial node.

On an existing instance, clearing `usb_mapping` removes the Compose device bind on the
next deploy but does not detach a Proxmox assignment already on the VM. Keep the mapping
configured until Remove App can run its ownership-checked detach, or use the guarded USB
detach procedure through a separately authorized operation.

Integration credentials are read from the hidden `integration_credentials` JSON field in
`homelab-infra/apps/<instance>` and rendered to a root-only `secrets.yaml`. They are never
accepted in `config/apps/<instance>.yml` or printed by Ansible.
