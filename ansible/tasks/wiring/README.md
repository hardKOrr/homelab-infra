# Wiring tasks

## OPNsense DNS account

Every app Deploy that writes DNS through OPNsense first includes
[`opnsense-dns-account.yml`](opnsense-dns-account.yml). The task owns the user
`homelab-infra-dns` with description `Owned by homelab-infra`, the direct privilege
`page-services-unbound`, no group memberships, and no shell or SSH key. It creates a
random, unusable interactive password on OPNsense and never reads or writes that password.

Account management uses the separate canonical Vaultwarden item
`homelab-infra/firewall-admin` (`api_key`, `api_secret`). That admin API key is used only by
this task. The existing `homelab-infra/firewall` item remains the credential for firewall
rules and aliases. Unbound records use `homelab-infra/dns` (`api_key`, `api_secret`). The
`firewall-admin` item must be seeded before any Deploy: this task consumes that pair but
does not create it or seed it into OPNsense. On the existing green firewall, create the
admin API key over the management SSH path and store its pair in that item. For a new
firewall, provision and store the pair through OPNsense's local administration path before
using this task. Issue [#566](https://github.com/hardKOrr/homelab-infra/issues/566) tracks
a future generated-`config.xml` bootstrap that will seed these credentials; the current
OPNsense playbook still uses the interactive installer and does not do that bootstrap.

When the owned user is first created, OPNsense returns its API key and secret once; the task
upserts both hidden fields into `homelab-infra/dns` before the app's record is written.
Later Deploys verify the stored pair still authenticates as this user and keep the same key.
An exact-name user with another description is foreign and stops the Deploy without being
changed. If an owned user's stored key is missing or no longer authenticates, the Deploy
fails without rotating it. To rotate deliberately, pass
`-e opnsense_dns_rotate_key=true` for one run. This deletes and recreates only the owned
DNS user, which revokes all of its prior API keys; the replacement pair is stored and
verified before the DNS record task proceeds. Remove that extra var from subsequent runs.

Changing API credentials leaves existing Unbound records in place. A failed Vaultwarden
write stops before record wiring. If the one-time secret was lost with that failed write,
rerun with the explicit rotation input to recover; existing DNS records stay in place.
