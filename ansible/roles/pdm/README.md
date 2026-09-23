# Proxmox Datacenter Manager (PDM)

PDM is a lab-scoped, official-ISO appliance VM. Deploy creates only a project-tagged VM and
never uses cloud-init, SSH, or QEMU guest-agent access. It refuses an occupied VMID unless
the VM carries both `_+lab` and `_<instance>` tags; it never adopts an existing system.

Before Deploy, upload a checksum-verified official ISO and name the exact Proxmox node,
storage, VMID, management IP, DNS/FQDN, bridge, CPU, memory, disk, and PBS storage in the
instance config. Complete the installer in the Proxmox console. The explicit endpoint is
`https://<fqdn>:8443`; PDM terminates TLS itself. Configure a certificate in PDM (ACME with the
lab's DNS challenge or a locally trusted certificate) and validate it before allowing clients to
connect. Do not publish it externally by default and do not add a Caddy route: the installer VM
has no project-owned reverse-proxy wiring.

`remotes` is the complete allow-list. Every remote declares type, nodes, authority, and
Vaultwarden item/hidden authid/token fields. Authority defaults to `read-only`; use an Auditor
credential. `mutating` is an explicit per-remote exception. Retrieve credentials from
Vaultwarden after installation and add only declared remotes through PDM. This repository does
not create tokens, update remotes, operate remote VMs, change clusters, or install remotes.

PDM configuration, including `remotes.cfg`, is under `/etc/proxmox-datacenter-manager/`.
**Backup PDM** creates a PBS-backed VM backup using the configured `backup.storage`; that image
contains the complete PDM configuration and remote definitions. **Restore PDM** requires the
exact VM backup volume and the literal confirmation `RESTORE-PDM`; it accepts only a backup named
for this VMID and replaces only the doubly tagged PDM VM. After restore, validate HTTPS and
read-only inventory, then retrieve or rotate remote tokens from Vaultwarden.

**Remove PDM** requires the literal confirmation `REMOVE-PDM`. It stops and destroys only the
doubly tagged PDM VM and withdraws its project guest record. It retains
`config/apps/<instance>.yml` and all Vaultwarden items as the restore point. It never alters,
unregisters, adopts, reconfigures, or otherwise contacts remote PVE, PBS, OPNsense, or other
guests.
