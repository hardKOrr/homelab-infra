# Home Assistant role

This role deploys Home Assistant as one Docker Compose service on the application's own
cloud-init Debian VM. USB passthrough is optional; an empty `app.usb_mapping` omits both the
Proxmox attachment and the Compose device binding. When configured, the named Proxmox USB
mapping must be declared dedicated and the guest device path must name its serial node.

Integration credentials are read from the hidden `integration_credentials` JSON field in
`homelab-infra/apps/<instance>` and rendered to a root-only `secrets.yaml`. They are never
accepted in `config/apps/<instance>.yml` or printed by Ansible.

When Caddy or Nginx Proxy Manager is registered, the role seeds `default_config:` only if
Home Assistant has no `configuration.yaml`, then adds a marked HTTP block that trusts the
registered proxy address for forwarded headers. Existing configuration is preserved. If
it already defines the `http` integration, deployment stops so its existing settings can
be merged deliberately instead of creating duplicate YAML keys.
