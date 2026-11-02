# `config/infrastructure.yml`

| Key | Required? | Default / notes |
|---|---|---|
| `domain` | required | shorthand for a single default estate |
| `domains` | optional | map of named estates — see [estates](estates.md) |
| `reverse_proxy.provider` | required | `caddy \| nginx \| none` |
| `reverse_proxy.instance` | required unless provider `none` | |
| `reverse_proxy.internal_cidrs` | required for Caddy internal routes | source CIDRs allowed to reach apps with `routing.access: internal` |
| `reverse_proxy.dns_challenge.provider` | recommended for Caddy ACME | `cloudflare` enables DNS-01 without public app records or WAN port forwarding; the token is external/Vaultwarden material |
| `reverse_proxy.dns_challenge.resolvers` | optional | non-empty list for SOA/zone discovery even with propagation_timeout=-1; defaults to the Caddy guest's gathered DNS servers, seeded from its selected network's dns_servers; explicit values must be reachable and return the public zone's SOA |
| `reverse_proxy.dns_challenge.propagation_timeout` | optional, default `-1` | disables only TXT propagation checking, not SOA discovery or CA validation; a positive timeout requires public TXT visibility and the authoritative-DNS access used by Caddy |
| `reverse_proxy.dns_challenge.propagation_delay` | optional, default `30s` | settling delay after TXT presentation; not proof of propagation |
| `sso.provider` | required | `authentik \| none` |
| `sso.instance` | required if provider `authentik`, else optional | |
| `notifications.provider` | required | `ntfy \| gotify \| discord \| none` |
| `notifications.instance` | required unless provider `none` | |
| `notifications.topic` | optional | |
| `notifications.webhook_url` | optional | |
| `dns.provider` | required | `pihole \| adguard \| opnsense \| none` |
| `dns.host` | required for external providers | not in Proxmox inventory |
| `dns.api_key` | optional | |
| `dns.instance` | optional | |
| `firewall.provider` | optional, default `none` | `opnsense \| none` — app Deploy opens each app's declared `firewall:` access and Remove withdraws it; the separate base-rules playbook runs only when `firewall.base.enabled: true` |
| `firewall.host` | required unless provider `none` | OPNsense API address |
| `firewall.interfaces` | required unless provider `none` | list of `{cidr, interface}`: the OPNsense interface (e.g. `opt4`) a guest's traffic enters on, matched by the guest's address |
| `firewall.wan_interface` | optional | default `wan`; where inbound port forwards are published |
| `firewall.sequence` | optional | default `1000`; baseline priority for app egress rules |
| `firewall.egress[].sequence` | optional | overrides the baseline for one narrowly scoped exception that must precede a policy rule |
| `firewall.validate_certs` | optional | default `false` |
| `firewall.base.enabled` | optional, default `false` | `true` lets `playbooks/maintenance/reconcile-firewall-base.yml` manage the lab-wide OPNsense rules; absent/false makes the playbook a no-op and app Deploy does not reconcile base rules |
| `firewall.base.aliases` | optional | map of alias name to a non-empty list of IPv4/IPv6 addresses or CIDRs; each becomes an OPNsense alias named `hli_<name>` |
| `firewall.base.rules[]` | optional | list of `{name, interface, source, destination, protocol, ports, action}`; source/destination are alias names or `any`; protocols are `TCP`, `UDP`, `TCP/UDP`, `ICMP` or `any`; IPv6-only ICMP aliases render as `IPv6-ICMP`, while one ICMP rule must not mix IPv4 and IPv6 aliases; `ports` is a list of destination ports/ranges, or `[]` for any; optional `sequence` sets OPNsense priority (default `500`) |
| `mail.provider` | optional, default `none` | `smtp \| none` — absent means disabled, exactly like an explicit `none`, so an existing checkout with no `mail:` block keeps passing `config-doctor.sh` unchanged |
| `mail.host` | required unless provider `none` | SMTP relay hostname; never in Proxmox inventory |
| `mail.port` | required unless provider `none` | typically `587` (STARTTLS) or `465` (implicit TLS) |
| `mail.encryption` | optional | `starttls \| tls \| none`; default `starttls` |
| `mail.from_address` | required unless provider `none` | the envelope/header From every app sends as |
| `mail.from_name` | optional | display name paired with `from_address` |
| `mail.username` | optional | SMTP AUTH username, often equal to `from_address` |
| `mail.password`, `mail.api_key`, `mail.api_secret`, `mail.token` | rejected | secret-shaped fields must never be authored here — `config-doctor.sh` fails the run; store the credential in `homelab-infra/mail` |
| `domains.<estate>.mail` | optional | non-secret overlay; an enabled block requires its own `from_address`, while omitted relay fields inherit the global `mail` block |
| `domains.<estate>.mail.provider` | optional | `smtp \| none`; overrides the global provider for that estate |
| `domains.<estate>.mail.host` | optional | estate-specific external SMTP relay hostname |
| `domains.<estate>.mail.port` | optional | estate-specific SMTP relay port |
| `domains.<estate>.mail.encryption` | optional | `starttls \| tls \| none`; inherits the global value when omitted |
| `domains.<estate>.mail.from_address` | required when the estate mail block enables SMTP | the estate's envelope/header From; this is what prevents the default estate's identity from leaking |
| `domains.<estate>.mail.from_name` | optional | estate display name |
| `domains.<estate>.mail.username` | optional | estate-specific SMTP AUTH username |
| `domains.<estate>.mail.password`, `api_key`, `api_secret`, `token` | rejected | credentials never belong in authored config; store a password in `homelab-infra/estates/<estate>/mail` when the relay needs one |
| `public_dns` | optional | lab-wide public address settings, shared by every estate that sets `domains.<estate>.public_dns.enabled`; the lab has one WAN, so one address source serves all estates; inert while no estate enables it |
| `public_dns.ttl` | optional, default `300` | positive integer record TTL in seconds, for every published record |
| `public_dns.families` | optional, default `[ipv4]` | non-empty list containing `ipv4`, `ipv6`, or both; AAAA is published only when `ipv6` is listed |
| `public_dns.allow_private` | optional, default `false` | permit private, loopback, link-local, or CGNAT (`100.64.0.0/10`) addresses; this is an explicit exception |
| `public_dns.address.source` | required when any estate enables public DNS | `static`, `lookup`, or `cname` |
| `public_dns.address.address` | required for `static` | IP address whose family must appear in `families`; private, loopback, link-local, and CGNAT addresses require `allow_private: true` |
| `public_dns.address.urls` | required for `lookup` | at least two HTTPS echo URLs; the updater uses the first that answers, and the Deploy preflight refuses to publish when they disagree |
| `public_dns.address.target` | required for `cname` | valid hostname outside every enabled estate's domain |
| `public_dns` and `.address` unknown keys | rejected | source-specific address keys are also rejected when they do not match `address.source` |
| `domains.<estate>.public_dns` | optional | turns public apex and wildcard resolution on for this estate; absent or disabled is inert |
| `domains.<estate>.public_dns.enabled` | optional, default `false` | when enabled, publish only `<ESTATE-domain>` and `*.<ESTATE-domain>` at the lab-wide address; there are no per-app records |
| `domains.<estate>.public_dns.provider` | optional | `github.com/caddy-dns/<provider>` module name; defaults to this estate's `dns_challenge.provider` and uses that challenge's credentials |
| `domains.<estate>.public_dns.adopt_existing` | optional, default `false` | allow a different existing record of the desired type to be updated; it never overrides an A/AAAA versus CNAME conflict |
| `domains.<estate>.public_dns` unknown keys | rejected | `ttl`, `families`, `allow_private` and `address` are lab-wide and belong in top-level `public_dns` |
| `maintenance.boot.order` | optional | default Proxmox startup tier for a guest that declares no `proxmox.boot_order`; `50`. `none` disables boot ordering lab-wide and leaves `startup` unset on every guest |
| `maintenance.boot.up` | optional | seconds Proxmox waits after a guest before starting the next tier; `15` |
| `maintenance.schedule` | optional | when the lab may be DISRUPTED — `always`, `never`, or a window `{days, start, duration}`. Defaults to nightly 04:00 for 120 minutes. Update cadence is unaffected; only reboots and container restarts wait for it. `never` is notify-only |
| `domains.<estate>.network` | optional | the network — and therefore the VLAN — this estate's guests are addressed on, keyed into `config/proxmox.yml` `networks:`. Absent, the estate's own name is tried and then `default`; see the network-selection order above |
| `domains.<estate>.maintenance.schedule` | optional | that estate's own window. Estates are independent clocks — a schedule is never merged or inherited ACROSS estates |
| `stacks.<name>.network` | optional | the network this stack's host is addressed on. Without it a stack declaring `shared: true` resolves to `shared`, and an estate-scoped stack to its estate's network |
| `stacks.<name>.maintenance.schedule` | optional | the window for everything on that stack host |
| `backups.datastore_path` | required | |
| `backups.schedule` | optional | |
| `backups.retention` | optional | |
| `backups.restore_bandwidth_mib_s` | optional | positive integer MiB/s restore limit passed as `--bwlimit` (KiB/s) by Restore Guest; absent or null omits the command option. This is a request to PVE, not a guaranteed cap: current PVE does not forward it to `proxmox-backup-client` for LXC restores from PBS |
| `vaultwarden.admin_token` | Seed/recovery override only | removed from authored config after cutover |
| `vaultwarden.instance` | optional | |
| `media_storage.library` | required for media apps with a `library_subpath` | the library root as the containers see it; `app.library_subpath` hangs off it |
| `media_storage.mounts` | required for media apps with a `library_subpath` | list of `{host, path}` — storage the node already mounts, attached to the stack host as Proxmox mountpoints. Nothing is created, exported or formatted |
| `stacks.<name>.{cores,memory,disk_volume}` | optional | deep-merged over `vars/stack-defaults.yml`; applies when the stack host is CREATED |
| `stacks.<name>.node` | optional | pins a new stack VM to this Proxmox node; an existing stack host stays on its current node |
| `stacks.<name>.storage` | optional | explicit Proxmox VM image storage for a new stack host; node-local storage limits placement to nodes that expose it |
| `stacks.<name>.pool` | optional | pool the stack host allocates from. A stack whose name matches a pool name inherits it without this key; a pool named here must exist on the network |
| `stacks.<name>.ip_address` | optional | pins the stack host to an exact address, honoured or refused with the conflict named |
| `stacks.<name>.boot_order` | optional | the stack host's Proxmox startup tier. A property of the stack, like its sizing — an app cannot set it, or the tier would depend on deploy order |
| `stacks.<name>.shared` | optional, default `false` | `true` means this stack host deliberately serves EVERY estate. See *Stack identity and estate isolation* below |

`playbooks/maintenance/reconcile-firewall-base.yml` is the only operation that reconciles
`firewall.base`. With the flag absent or false it is inert; ordinary app Deploy and Remove
continue to manage only their `homelab-infra: <instance>` rules. When enabled, declared
aliases are named `hli_<name>` and owned rules are described `homelab-infra: base <name>`.
The runner must retain access to the firewall management API under the declared policy;
when a private-deny rule covers its VLAN, include a narrow runner-to-firewall API pass
ahead of that deny.
Aliases removed from `firewall.base.aliases` remain in OPNsense; remove an unused `hli_<name>` alias manually after checking its references.
A rule with multiple `ports` emits one filter rule per port, adding `port <number>` to its
description to keep each rule identifiable. The first enabled run replaces only rules with
the legacy `Green lab: ` description prefix; the temporary `Green lab: operator to Caddy`
rules remain untouched. To keep rollback backups inside OPNsense's 100-entry history, the
playbook uses two transactions, each capped at 80 model writes: reconcile and verify the
owned aliases/rules, then remove and verify the replaced legacy rules. It records a fresh
newest local config-backup ID immediately before each transaction with the
`homelab-infra/firewall-admin` API key. If a write, apply or readback fails while the
management API answers, it restores that transaction's backup and applies the restored
config before failing the run. A failure in legacy cleanup therefore leaves the verified
owned rules and the original legacy rules together. If the API stops answering, the failure
names that transaction's backup ID for console recovery.

### Stack identity and estate isolation

A **stack** is a bare concept — `media`, `sso`, `monitoring` — and never a hostname or a
tag. Both are derived from it, and both add the estate when the lab has more than one:

| | single estate (or no `domains:` map) | two or more estates |
|---|---|---|
| effective identity | `media` | `media-<estate>` |
| Proxmox hostname | `stack-media` | `stack-media-<estate>` |
| Proxmox tag | `_.stack+media` | `_.stack+media-<estate>` |
| inventory group | `lab_stack_media` | `lab_stack_media_<estate>` |

The suffix rule is the repository's existing instance-naming rule applied to a host: one
estate means no suffix; two or more means every estate-scoped identity carries its estate.

**Merge behaviour.** `shared` merges exactly like the rest of the stack block —
`vars/stack-defaults.yml` supplies the git-managed value and
`config/infrastructure.yml`'s `stacks:` map is deep-merged over it
(`combine(recursive=True)`). It is resolved once per placement, before the lookup that
decides whether a host already exists, because it decides *which* host the app is looking
for. No app-level key overrides it: a stack host is shared by definition, so one tenant
must not be able to move it out from under the others.

**What `shared: true` does.** The identity takes no estate suffix, so every estate
resolves to one host, and that host is tagged `_.shared`. Both halves come from this one
declaration. Nothing infers the tag after the fact from where applications happened to
land; an ordinary hosting unit hosts exactly one estate, and there is no path by which an
app that did not declare sharing reaches another estate's host.

`_.shared` means deliberate cross-estate hosting, not lab-wide application scope.
`scope: lab` does not imply the tag. A hosting substrate that accepts estate-scoped
applications from more than one estate must declare `shared: true`; a native guest that
hosts only its lab-scoped application does not.

### Maintenance schedules

`maintenance.schedule` is one primitive, resolved through the same chain as everything
else: **global → estate → stack → app**. `always` means "disrupt whenever it is needed",
a window means "only then", and `never` means notify-only. There is no separate class or
mode enum, because a schedule already expresses all three.

A narrower layer **replaces** what it inherits, whole. Half-merging two windows produces
a third window nobody declared, and the operator would have no way to read what a guest
will actually do. This mirrors the same decision for estates in [§3](../CONTRACT.md#3-canonical-homelabinfra_infra-shape).

An app declares its own in `config/apps/<instance>.yml`:

```yaml
maintenance:
  schedule: never                # or always, or {days:, start:, duration:}
```

Several apps on one guest **intersect** rather than compete: a shared stack host may only
reboot inside every one of their windows, one app scheduled `never` holds the whole guest,
and two windows that never overlap are reported as a conflict rather than settled by
picking a winner. Container restarts stay finer-grained — Watchtower restarts one
container, so it follows that app's own schedule.

A resolved schedule is **enforced by the guest**, not by anything watching it. It becomes
a systemd `OnCalendar` in `homelab-maintenance.timer` on the guest, written at deploy time,
and the guest reboots itself when the window opens — and only if `/var/run/reboot-required`
is actually there. `never` removes the timer, which is what notify-only means. Nothing
polls: a job asking "is it time yet" on a fixed interval would re-implement the schedule on
top of itself and would miss any window that opened while the control plane was down.

`ansible/scripts/maintenance-schedule.py` owns the arithmetic,
`ansible/tasks/maintenance/resolve-schedule.yml` is the seam, and
`ansible/tasks/maintenance/install-guest-timer.yml` is the enforcement point. Consumers read
the resolved answer (`mode`, `due`, `text`, `cron`, `oncalendar`, `monitor_only`,
`next_open`, `conflict`) and never re-derive it.
