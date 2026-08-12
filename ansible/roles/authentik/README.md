# authentik role

Docker Compose deployment of Authentik (server + worker + PostgreSQL + Redis) on a
stack host. Deployment mechanics are documented in `tasks/main.yml`; this README
records where the role's responsibility ends.

## Scope

The role provisions the service and hands back its API endpoint, admin credentials
and token. **Directory content is operator policy, not deployment state** — account
names, group membership, social login sources and MFA enforcement are set in the
Authentik UI. The role creates none of them, so a re-deploy never overwrites a
decision made there.

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

**Membership is not managed here.** Who is in the group is operator policy, set in the
Authentik UI, and neither the first deploy nor any re-deploy touches it. Same for
account names, social login sources and MFA — see the boundary at the end of
[403/notes.md](../../../.claude/meta/403-app-authentik/notes.md).
