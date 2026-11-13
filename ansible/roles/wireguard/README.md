# WireGuard

Deploy uses native wg-quick by default. Docker hosting uses the stack selected by
`stack`, with its LXC or VM guest type; the host kernel must provide WireGuard.
The endpoint is UDP, with no HTTP route or login page. Shared wiring creates a DNS
record pointing directly at the guest and a Kuma ping monitor for host reachability;
the monitor does not verify a WireGuard handshake.

For Docker, Backup App archives the container's complete config tree while its service
is stopped. Restore App previews the PBS point, then uses the shared restore swap to
replace that tree and verify the managed interface. Keys remain private in PBS and in
the canonical Vaultwarden item. WireGuard has no database.

The instance's authored peer declarations and Vaultwarden credentials remain the source
for the managed interface on the next Deploy. Keep them consistent with the recovery
point. Additional persistent tunnel configurations in `config/wg_confs/` are also
archived. Native recovery is not implemented by these Docker hooks.

Use the [recovery drill](../../playbooks/maintenance/recovery-drill.md) and the generic
Remove App playbook for Docker; it removes the instance's Compose project and preserves
the stack host and other applications.
