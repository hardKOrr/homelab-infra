# Home Assistant role

This role deploys Home Assistant as one Docker Compose service on the application's own
cloud-init Debian VM. USB passthrough is optional; an empty `app.usb_mapping` omits both the
Proxmox attachment and the Compose device binding. When configured, the named Proxmox USB
mapping must be declared dedicated and the guest device path must name its serial node.

With `stack: home-assistant-lxc` (and `infrastructure.stacks.home-assistant-lxc.guest_type: lxc`)
it runs the same Compose project on that stack's Docker LXC instead; the USB mapping must then
be declared shared; the seam checks that the mapped device owns exactly `usb_device_path` and
binds that serial node through the LXC and into the container.

On an existing instance, clearing `usb_mapping` removes the Compose device bind on the
next deploy but does not detach a Proxmox assignment already on the VM. Keep the mapping
configured until Remove App can run its ownership-checked detach, or use the guarded USB
detach procedure through a separately authorized operation.

Integration credentials are read from the hidden `integration_credentials` JSON field in
`homelab-infra/apps/<instance>` and rendered to a root-only `secrets.yaml`. They are never
accepted in `config/apps/<instance>.yml` or printed by Ansible.

`app.initial_configuration` optionally seeds `configuration.yaml` on a fresh instance.
Include `default_config: {}` and, behind Caddy, `http.use_x_forwarded_for: true` with
`http.trusted_proxies` listing the proxy's address. Deploy never replaces an existing
file; subsequent configuration belongs to Home Assistant and travels in its backup.
