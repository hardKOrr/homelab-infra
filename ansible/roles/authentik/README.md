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

Still set in the UI, because this repo has no config surface for them yet: social
login sources, MFA and authenticator stages, and flow doctrine.

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
no way to override them; the answer to that is a config surface, which `directory:`
now is. Sources and MFA do not have one yet and remain UI-only.
