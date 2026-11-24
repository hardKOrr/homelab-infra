# OPNsense appliance

[`opnsense.yml`](../../playbooks/apps/opnsense.yml) imports a checksum-pinned amd64 nano
image. The instance declares its VMID, separate WAN/LAN bridges and bootstrap LAN address
in [`opnsense.example.yml`](../../../config.example/apps/opnsense.example.yml).
The runner must reach that LAN address. The role never adopts the estate's external
firewall, and an occupied VMID must match the instance name and both ownership tags.
Existing disks and wiring are preserved; configuration drift fails before starting.

`tasks/credential.yml` generates missing API pairs in the canonical `firewall-admin`,
`firewall` and `dns` Vaultwarden items, preserving existing values. A partial pair fails.
The root UI password lives in `homelab-infra/apps/<instance>` as `admin_password`.
`templates/config.xml.j2` seeds separate API users with hashed secrets and direct
privileges; the firewall privileges come from the shared account contract.

`tasks/seed.yml` uses checksum-pinned UFS2Tool v4.1 (self-contained Linux amd64) on the
Proxmox node to add `/conf/config.xml` to a private copy of the nano image before import.
It needs `bzip2` and `unzip`. Stock OPNsense's importer is interactive, so this path does
not attach a config disk or use the console. The root-only staging directory is removed
on success and failure. An existing boot disk is never reseeded, including after Restore.

The shared guest-start operation skips guest-agent exec readiness for this appliance;
`tasks/configure.yml` instead waits for the API to accept the saved admin key. The
existing account and enabled base-rule reconciles target this instance's LAN address,
without changing the estate's configured firewall address.

An isolated VM can set `bootstrap.firewall_base` to override the estate's base-rule
inputs for this Deploy only (for example, rules on `lan` instead of estate-only OPT
interfaces). The shared reconciler still applies the enabled contract. Omitted fields
inherit the estate defaults through a recursive merge.

`tasks/drill-canary.yml` saves and checks an API alias with a documentation address.
Its description is `drill-canary-<id>`; its legal alias name uses an underscore prefix
and a hash because OPNsense limits names to 31 alphanumeric or underscore characters.
