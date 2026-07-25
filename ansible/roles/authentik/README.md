# authentik role

Docker Compose deployment of Authentik (server + worker + PostgreSQL + Redis) on a
stack host. Deployment mechanics are documented in `tasks/main.yml`; this README
records the account/hostname doctrine the role enforces and the operator steps it
cannot automate.

## Account doctrine

- **Standing admin is `collector`.** Authentik's `AUTHENTIK_BOOTSTRAP_*` env vars
  always create `akadmin`, so the role renames that user to `collector` via the API
  right after the first start. The generated password and the API token survive the
  rename — only the sign-in name changes. Re-runs accept either name, so labs that
  predate the rename keep working and converge on the next deploy.
- **Standing groups exist from deploy time.** `homelab-users` (bound to every wired
  application by `tasks/wiring/authentik.yml`) and `homelab-admins`
  (`is_superuser: true`) are created by the role, so the wiring's "binding lands on
  the next wire" fallback is the exception, not the normal path. Membership is
  managed by the operator in the Authentik UI.
- **Canonical hostname is `auth.<domain>`.** `routing.subdomain: auth` in
  `vars/app-defaults/authentik.yml` — the instance name stays free for stack and
  inventory identity. Labs migrating from `<instance>.<domain>` can set
  `routing.wire_instance_alias: true` in `config/apps/<instance>.yml` to keep the
  old hostname routed during the transition.

## Google source (optional)

Set both `app.google_client_id` and `app.google_client_secret` in
`config/apps/<instance>.yml` and the role configures a Google OAuth source
(slug `google`, `user_matching_mode: email_link`) at deploy time. Leave them empty
and no source is created. Redirect URI to register in Google Cloud Console:
`https://auth.<domain>/source/oauth/callback/google/`.

## Operator steps (not automatable)

- **MFA enrollment**: enforcing MFA is a flow-policy decision made in the Authentik
  UI (Flows → default-authentication-flow → add an authenticator validation stage,
  or per-group enrollment). The role does not modify stock flows.
- **Group membership**: add real users to `homelab-users` / `homelab-admins`.
- **Token rotation**: the recorded API token belongs to the admin account; rotate it
  in the UI and update `sso.token` in `config/.generated/facts.yml` if compromised.

## Identity modes served

Apps register per `routing.identity` (see `ansible/vars/CONTRACT.md`): `catalog`
(Application tile only), `oidc` (OAuth2 provider + Application; client credentials
handed back to the app deploy), `forward_auth` (proxy provider + embedded outpost;
proxy-side enforcement lands with slice 306), `none` (no Authentik object).
