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

## Native

With `hosting.method: native` and `proxmox.guest_type: lxc` or `vm`, Home Assistant runs on
its own guest from a virtual environment built from the pinned upstream source release
(`app.native_version`, checksum `app.native_sha256`) under the `<instance>` systemd unit.
Upstream documents only the OS and Container installs, so the build installs the
release's pinned requirements and the source into a standalone Python of the version the
release requires, and Home Assistant installs integration requirements itself on first
use. The config directory is the single recovery archive, backed up and restored through
the shared PBS client and restore swap with the unit as the quiesced service. A USB
coordinator reaches a VM by its dedicated mapping and an LXC by the shared LXC seam.

## Kubernetes

With `hosting.method: kubernetes` Home Assistant is one pod in the app's namespace. `app.config_storage`
sizes its default-StorageClass config claim, the only `data` recovery archive; the shared
CronJob writes it and the shared Job puts it back. `configuration.yaml` is seeded once
(`initial_configuration`, or a minimal `http` block trusting `app.kubernetes_trusted_proxies`
so the ingress works), after which Home Assistant owns it. `secrets.yaml` is refreshed from a
Secret at each pod start, so restart the pod after changing integration credentials.
The default seed includes the automation editor's `automations.yaml`; an explicit
`initial_configuration` must include it to use editor-managed automations.

During first-run setup, confirm the imported HTTP server settings under Settings →
System → Network within five minutes. Home Assistant otherwise reverts proxy trust and
returns HTTP 400 through ingress; restore and confirm the proxy settings from direct
access if the timer expires. See [Home Assistant HTTP settings](https://www.home-assistant.io/integrations/http/).

A configured `usb_mapping` is attached to the first cluster node's VM and the pod is pinned
to that node with the serial node bound in. That node cannot move the pod, and attaching may
restart the VM.

Method limit: discovery that needs host networking (mDNS, SSDP, DHCP) does not cross the pod
network, so integrations that rely on it need their address entered by hand.
