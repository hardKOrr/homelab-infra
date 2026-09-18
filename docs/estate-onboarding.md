# Runbook: onboard a new estate

An estate is a named domain boundary, not just another TLS name. It has its own
domain, identity provider, and (when selected) DNS and ACME credentials. The
reverse proxy and the other explicitly shared platform services remain shared.

This is the operator path for adding an estate before placing real workloads on it.
Use [`ansible/vars/CONTRACT.md`](../ansible/vars/CONTRACT.md) §3 and §5 for the
schema and overlay rules; this runbook does not duplicate that contract. For the
measured second-estate rollout and its defects, see
[`docs/meta/done/008-estate-contract/`](meta/done/008-estate-contract/).

## 1. Plan the cutover and the names

Choose a lowercase estate name using the contract's naming rules, and decide which
domain, DNS provider, ACME provider, network band, and Authentik instance it will use.
Before changing the repository, make sure the operator-owned prerequisites are ready:

- the public DNS zone and its delegation are in place for the domain;
- the ACME provider token is scoped to the zone and can be stored in Vaultwarden;
- any LAN DNS server, router, VLAN, route, and firewall changes have an owner; and
- the existing estate's current name will become the one `default: true` entry.

The router, VLAN, firewall, and public DNS zone are outside this repository's authority.
The Ansible network resolver only selects among networks already declared by the
operator; it does not create any of those prerequisites.

## 2. Complete the instance rename wave before declaring two estates

This step applies when a single-estate `domain:` setup becomes a `domains:` map with
two or more entries. It does not apply to a lab that still has only one estate.

For every application whose catalog entry has `scope: estate`, rename the existing
estate's files in `config/apps/` to the multi-estate form:

```text
<app>-<estate>[-<variant>].yml
```

For example, if `personal` is the existing default estate, rename
`radarr.yml` to `radarr-personal.yml` and `radarr-4k.yml` to
`radarr-personal-4k.yml`. Lab-scoped applications keep their unsuffixed names.
Update any configuration pointer such as `sso.instance` when the default Authentik
file is renamed. Do not copy jobs or invent an alternate instance naming scheme.

Prepare all of these renames and the domain-map edit together, review them, and make
the rename wave one deliberate commit. At minimum, do not declare the two-estate map
first and leave the old filenames for a later cleanup. If the changes must be split,
land the complete rename wave before the map and do not re-render jobs between them.

`ansible/scripts/app-instances.py` treats a stale estate-scoped filename as
non-canonical, prints the offending path, and exits **1**. That failure prevents the
Rundeck instance lists and jobs from being rendered. The normal
[Rundeck instance workflow](../rundeck/README.md#instances-are-a-dropdown-not-a-memory-test)
will work again only after every estate-scoped file has the required prefix.

## 3. Declare the estate map

Keep the existing top-level `domain:` value as the compatibility/default value, and
add the named map. The default entry must describe that same existing estate:

```yaml
domain: "personal.example.com"

domains:
  personal:
    domain: "personal.example.com"
    default: true
  cedar:
    domain: "cedar.example.com"
```

A map with two or more entries must have exactly one `default: true`; declaration
order never selects the default. Apps without `routing.estate` continue to select
the default estate. The existing estate's runtime behavior is otherwise unchanged:
its domain, top-level default facts, and shared services are not duplicated or
replaced merely by adding the map. No application is copied into the new estate, no
guest is moved, and no DNS or network resource is created by this edit alone.

After the rename wave and this declaration are committed, run the normal configuration
validation and Rundeck job re-render. Treat any `app-instances.py` exit 1 as an
incomplete rename, not as a reason to bypass the check.

## 4. Deploy the estate's Authentik first

Create the new estate's ordinary Authentik instance, for example
`config/apps/authentik-cedar.yml`, with the estate selector:

```yaml
routing:
  estate: cedar
```

Deploy it through the normal **Deploy Authentik** job. It is an estate-scoped app, so
the stack and instance identity carry the estate suffix. Deploying with
`routing.estate: cedar` records the estate's SSO facts under
`estates.cedar.sso` and wires its `auth.<domain>` route against that Authentik. The
default estate's `sso` entry and credentials must remain separate.

The Authentik role writes generated administrator and continuity material to the
estate's canonical Vaultwarden item:

```text
homelab-infra/estates/cedar/sso
```

Do not copy the platform item's token into this item and do not put generated secrets
in `config/`. Confirm the estate item exists and is readable by the next run before
deploying an app that uses the estate's SSO.

## 5. Configure ACME DNS-01 and the Caddy wildcard

For publicly trusted certificates, add the estate's provider and non-secret options
under `domains.<estate>.dns_challenge`. For example:

```yaml
domains:
  cedar:
    domain: "cedar.example.com"
    dns_challenge:
      provider: cloudflare
```

Store the provider credential separately in:

```text
homelab-infra/estates/cedar/reverse_proxy
  dns_api_token
```

After Vaultwarden cutover, use the Rundeck **Store Secret** job (the item is entered
without the `homelab-infra/` prefix, such as
`estates/cedar/reverse_proxy`) rather than committing the token to
`config/infrastructure.yml`. The separate estate DNS-record credential, if needed,
belongs in `homelab-infra/estates/cedar/dns`; do not merge it with the ACME item.
The [variable contract](../ansible/vars/CONTRACT.md#5-required-vs-optional-keys-per-config-file)
defines the complete field and inheritance rules.

Re-run **Deploy Caddy** after the map and token are available. Caddy is the one shared
TLS edge. It creates a DNS-01 policy for the estate's domain tree and requests the
estate wildcard (`*.cedar.example.com`, with the apex covered by the same policy).
Routes in that estate use that wildcard; individual apps do not each issue a new
certificate. The policy is per estate, so the cedar token is not used to issue names
for another estate. DNS-01 validation is performed by the public ACME service; it does
not require opening an inbound WAN port or making the application names public.

## 6. Populate LAN DNS for the estate

If the lab manages records through Ansible, declare the non-secret record-wiring
provider and host under `domains.cedar.dns`:

```yaml
domains:
  cedar:
    domain: "cedar.example.com"
    dns:
      provider: opnsense       # or pihole / adguard / none
      host: "192.168.1.1"
```

The credential normally comes from the global DNS Vaultwarden item when one DNS
server serves the lab. Use `homelab-infra/estates/cedar/dns` only when this estate
uses a different DNS server, with the fields required by the contract. In that
per-estate-credential case, verify that the runtime registry contains
`estates.cedar.dns`; when the estate inherits the global DNS item, do not duplicate
that credential just to create a second item — verify that the estate overlay combines
the global credential with this authored provider block. In either case, verify that
the generated host records point at the shared reverse proxy. The authoritative
distinction between authored `domains.<estate>.dns` and the runtime estate overlay is
in [CONTRACT §3](../ansible/vars/CONTRACT.md#3-canonical-homelabinfra_infra-shape).

Do not skip this step just because the ACME check passed. Without a LAN DNS record,
names such as `auth.cedar.example.com` and `app.cedar.example.com` return no usable
answer from inside the lab (commonly NXDOMAIN/SERVFAIL), so a browser on the LAN
cannot reach them by name. Public TLS is unaffected: DNS-01 can still validate from
the internet, making the estate appear healthy from outside while it is unreachable
by name inside.

## 7. Keep shared services shared, and place workloads deliberately

The estate boundary is not a request to deploy a second copy of every platform
service. The registry keys that remain global are `reverse_proxy`, `notifications`,
`monitoring`, `metrics`, `backups`, and `vaultwarden`; the estate-bound keys are the
domain and the estate's SSO, with DNS selected per estate when configured. See
[CONTRACT §3](../ansible/vars/CONTRACT.md#3-canonical-homelabinfra_infra-shape)
instead of maintaining a second role-key table here.

Use the catalog and stack declarations to decide placement:

- `scope: estate` means one app deployment per estate and the suffixed instance name.
- `scope: lab` means one deployment serves every estate. It is the deliberate
  lab-wide exception; it does not by itself give a hosting guest the `_.shared` tag.
- A stack with `shared: true` is the explicit cross-estate hosting exception. It uses
  one unsuffixed stack host, tags it `_.shared`, and places it on the shared network
  when that network exists. An ordinary stack remains estate-specific, even when an
  app on it is important to the whole lab.

Do not infer sharing from where an app happened to land. A stack that will host apps
from multiple estates must declare `shared: true`; the declaration, not deployment
order, determines the host identity and network. The [contract's stack section](../ansible/vars/CONTRACT.md#stack-identity-and-estate-isolation)
has the identity and merge details.

If the estate needs a distinct mail identity, keep that work separate and follow
[the per-estate mail issue](https://github.com/hardKOrr/homelab-infra/issues/267)
when it lands.

## 8. Optionally segment the networks

Treat the estate boundary as a network boundary when the lab is ready to enforce it.
`ansible/tasks/network/resolve-network.yml` is advisory: it selects a declared
network, but it never creates a VLAN, bridge, route, or firewall rule. A flat lab with
only `networks.default` therefore remains flat and safe to onboard.

To segment incrementally, add a `networks.shared` band first for lab-wide services,
then add a band for the estate (or set `domains.cedar.network` to the name of an
already-declared band), and redeploy the guests that should move into each band. The
resolver applies the shared/estate hints only when those names exist, so a lab can
address one band at a time before the final VLAN split. Coordinate the corresponding
router, gateway, DNS, route, and firewall changes outside this repository. See
[CONTRACT §5](../ansible/vars/CONTRACT.md#5-required-vs-optional-keys-per-config-file)
and [`resolve-network.yml`](../ansible/tasks/network/resolve-network.yml) for the
selection order.

## 9. Prove the estate before moving real workloads

Record the result of this checklist before onboarding user data or the first real
workload:

- [ ] `domains` contains the existing estate and the new estate, exactly one entry is
      `default: true`, and the top-level compatibility `domain` still names the
      default domain.
- [ ] Every `scope: estate` instance file uses
      `<app>-<estate>[-<variant>]`; `app-instances.py` exits 0 and Rundeck jobs render
      with the expected estate-labelled options.
- [ ] The new Authentik deploy used `routing.estate: <estate>`, its
      `estates.<estate>.sso` facts are present, and
      `homelab-infra/estates/<estate>/sso` is distinct from the platform item.
- [ ] Caddy has the estate's DNS-01 policy and wildcard; obtain the certificate and
      open the Authentik URL over HTTPS without a certificate error.
- [ ] From a LAN client, resolve and open `auth.<estate-domain>` and one disposable
      canary app name. Confirm the names reach the shared Caddy address.
- [ ] Deploy one disposable estate canary with `routing.estate: <estate>`; verify its
      route and Authentik object exist in the new estate, not in the default estate.
      Remove the canary through the normal job after the checks.
- [ ] Confirm Caddy, notifications, monitoring, metrics, backups, and Vaultwarden
      remain the intended single shared services, and that no unintended second stack
      host was created.
- [ ] If segmentation is enabled, verify guest addresses and reachability against the
      declared shared/estate bands. If the lab is still flat, record that the flat
      layout is intentional and defer VLAN/firewall acceptance to the operator-owned
      network change.
- [ ] Re-run the configuration and repository gates before real workloads move onto
      the estate.
