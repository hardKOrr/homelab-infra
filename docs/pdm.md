# Proxmox Datacenter Manager (PDM)

PDM is a lab-scoped, official-ISO appliance VM. Deploy creates only a project-tagged VM and
never uses cloud-init, SSH, or QEMU guest-agent access. It refuses an occupied VMID unless
the VM carries both `_+lab` and `_<instance>` tags; it never adopts an existing system.

Before Deploy, upload a checksum-verified official ISO and name the exact Proxmox node,
storage, VMID, management IP, DNS/FQDN, bridge, CPU, memory, and disk in the instance config.
Complete the installer in the Proxmox console. PDM listens on HTTPS port 8443. Use an internal
Caddy route for `https://pdm.<lab-domain>` only after installation and certificate validation;
do not publish it externally by default.

`remotes` is the complete allow-list. Every remote declares type, nodes, authority, and
Vaultwarden item/hidden authid/token fields. Authority defaults to `read-only`; use an Auditor
credential. `mutating` is an explicit per-remote exception. Retrieve credentials from
Vaultwarden after installation and add only declared remotes through PDM. This repository does
not create tokens, update remotes, operate remote VMs, change clusters, or install remotes.

PDM configuration, including `remotes.cfg`, is under `/etc/proxmox-datacenter-manager/`.
Protect the complete PDM VM under the normal PBS VM backup schedule; take an extra VM backup
or snapshot before risky changes. Restore the VM from PBS, validate HTTPS and remote inventory,
then retrieve/rotate tokens from Vaultwarden. To remove, first remove PDM wiring and then stop
and destroy only the doubly tagged PDM VM; retain its instance config and Vaultwarden items.
Never alter or unregister remote PVE, PBS, OPNsense, or other guests during removal.
