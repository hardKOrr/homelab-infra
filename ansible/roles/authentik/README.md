# authentik role

Docker Compose deployment of Authentik (server + worker + PostgreSQL + Redis) on a
stack host. Deployment mechanics are documented in `tasks/main.yml`; this README
records where the role's responsibility ends.

## Scope

The role provisions the service, applies the declared directory, and hands back its
API endpoint, admin credentials and token.

**Groups and accounts are configuration** — declare them under `directory:` in
`config/apps/<instance>.yml` and `tasks/directory.yml` creates them and keeps them
correct through the API on every deploy. What is declared is managed; what is not
declared is never read into, never patched and never deleted, so an account made in
the UI keeps working and removing an entry from config stops managing it rather than
deleting a person.

Account passwords are generated, stored in the estate's canonical Vaultwarden item as
`user_<username>_password`, and applied once at creation — a password its owner
changed in the UI survives every re-deploy. `rotate_password: true` on an account
forces a new one for that run.

**So is the rest of the identity surface.** Four more keys in the same file, each
applied by the task file named after it, each a no-op when absent:

| Key | File | What it declares |
|---|---|---|
| `flows` | `tasks/flows.yml`, `tasks/flow-bindings.yml` | flow instances and the stages bound into them |
| `authenticators` | `tasks/authenticators.yml` | TOTP / WebAuthn / static / Duo / SMS setup stages, and the validation stage that decides whether MFA is required |
| `sources.oauth` | `tasks/sources.yml` | "Sign in with Google / GitHub / …" login sources |
| `brands` | `tasks/brands.yml` | per-domain login page identity and its flows |

They share the directory's contract exactly: declared is created and kept correct,
undeclared is never touched and never deleted. The one exception is `stages_exact:
true` on a flow, which deletes bindings the declaration does not list — off by
default, and the only destructive option in the role.

Fields are passed to the API verbatim rather than enumerated, so a field a future
Authentik release adds is configurable the day it ships. Only the keys that name
another object by name — `configure_flow`, `configuration_stages`, a source's or a
brand's flow slugs, a binding's `stage` — are resolved to internal ids here, and each
one asserts its target exists before anything is written.

OAuth client secrets never appear in config: `consumer_key` is a public identifier
and lives beside the rest, while the matching secret is read from the estate's
canonical Vaultwarden item as `source_<slug>_secret`, the same shape Caddy's
`dns_api_token` uses.

The admin account is `akadmin`, created by `AUTHENTIK_BOOTSTRAP_*` on first start.
Its generated password and API token are stored in `homelab-infra/sso` under
`admin_password` / `token`. Rename it in the UI if you like — the
role only asserts the recorded token still belongs to it, and that assert names the
account it expected, so a rename shows up as an actionable failure rather than a
silent one.

## Hostname

`routing.subdomain: auth` in `vars/app-defaults/authentik.yml` — a default, not a
rule. Override it in `config/apps/<instance>.yml` like any other routing key; the
instance name stays free for stack and inventory identity. Multi-estate labs get
one Authentik per estate, each at its own estate's `auth.<domain>`.

## Identity modes served

Apps register per `routing.identity` (see `ansible/vars/CONTRACT.md`): `catalog`
(Application tile only), `oidc` (OAuth2 provider + Application; client credentials
handed back to the app deploy), `forward_auth` (proxy provider + embedded outpost;
proxy-side enforcement lands with slice 306), `none` (no Authentik object).

The group named by `wiring_auth_group` (default `homelab-users`) is bound to each
wired Application, and the wiring **creates the group** when it is absent — empty and
never a superuser. A platform that names a group in every app playbook has to be the
thing that creates it; the alternative was every deploy on a fresh lab failing until
someone made it by hand.

**Membership is declared, not discovered.** Put the group in an account's `groups:`
list under `directory:` and this deploy makes that account's membership match the
declaration. The wiring's own group creation above stays as the safety net for a lab
that declared no directory at all, or that renamed `wiring_auth_group` without
declaring the new name.

[403/notes.md](../../../docs/meta/done/403-app-authentik/notes.md) ends with a wider
boundary than this — "account names, group membership, social login sources and MFA
enforcement are operator policy". That note is superseded for groups and accounts.
What it was really rejecting was one lab's choices hardcoded as product defaults with
no way to override them; the answer to that is a config surface, which `directory:`,
`flows:`, `authenticators:`, `sources:` and `brands:` now are. The boundary that
remains is the one the note should have drawn in the first place: the platform ships
no lab's content as a default, and every object it creates is one the operator
declared.
