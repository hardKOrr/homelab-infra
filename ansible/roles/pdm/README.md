# Proxmox Datacenter Manager (PDM)

PDM is a lab-scoped, official-ISO appliance VM. Deploy creates only a project-tagged VM and
never uses cloud-init, SSH, or QEMU guest-agent access. It refuses an occupied VMID unless
the VM carries both `_+lab` and `_<instance>` tags; it never adopts an existing system.

Before Deploy, upload a checksum-verified official ISO and name the exact Proxmox node,
storage, VMID, management IP, DNS/FQDN, bridge and optional VLAN tag, CPU, memory, disk,
and PBS storage in the instance config. Complete the installer in the Proxmox console.
Use the selected network's mask, gateway, and DNS settings from `config/proxmox.yml` in
the manual installer. The explicit endpoint is
`https://<fqdn>:8443`; PDM terminates TLS itself. Deploy registers the declared DNS record,
internal Caddy route, optional Authentik catalog entry, and Kuma monitor. Caddy uses HTTPS to
the appliance and skips verification of PDM's installer certificate; client HTTPS terminates
at Caddy. The VM boots from its system disk first and falls back to the installer ISO, so
repeat Deploy and a restored appliance keep booting the installed system. Keep the route
internal unless the instance explicitly declares another access mode.

`remotes` is the complete allow-list. Every remote declares type, nodes, authority, and
Vaultwarden item/hidden authid/token fields. Authority defaults to `read-only`; use an Auditor
credential. `mutating` is an explicit per-remote exception. Retrieve credentials from
Vaultwarden after installation and add only declared remotes through PDM. This repository does
not create tokens, update remotes, operate remote VMs, change clusters, or install remotes.

When OPNsense is configured, Deploy opens TCP 8006 from the PDM guest only to the declared
PVE remote nodes. Node names resolve through `proxmox.nodes`; other hostnames resolve through
the runner. The PDM rules use `firewall.proxmox_api_sequence` (default `250`) so this exact
exception can precede a broader private-network deny; choose a value below that deny when
needed. Deploy also adds a marked inbound TCP 8006 allow from the PDM guest address to
locally managed nodes declared by those remotes, ahead of the node's default inbound drop
policy. External remotes receive no node firewall change. Remove withdraws only the
PDM-owned OPNsense egress and Proxmox node firewall rules.

PDM configuration, including `remotes.cfg`, is under `/etc/proxmox-datacenter-manager/`.
**Backup PDM** creates a PBS-backed VM backup using the configured `backup.storage`; that image
contains the complete PDM configuration and remote definitions. **Restore PDM** requires the
exact VM backup volume and the literal confirmation `RESTORE-PDM`; it accepts only a backup named
for this VMID and replaces only the doubly tagged PDM VM. After restore, validate HTTPS and
read-only inventory, then retrieve or rotate remote tokens from Vaultwarden.

**Remove PDM** requires the literal confirmation `REMOVE-PDM`. It removes the PDM route,
Authentik entry, monitor and DNS record, then stops and destroys only the doubly tagged PDM VM
and withdraws its project guest record. It retains
`config/apps/<instance>.yml` and all Vaultwarden items for recovery. Remote systems are untouched.
