# 505 — build notes

## Why one role for five apps

`AGENTS.md` describes `roles/<app>/` — one role per deployable app — and this slice ships
`roles/servarr/` instead. The reasoning, on record so it is not re-litigated:

- The five apps differ in exactly three facts (API version, Prowlarr implementation name,
  download-client category field) and all three are **already** a table:
  `ansible/vars/media-wiring.yml`, written by slice 504. Five roles would mean a second
  description of the same five apps, kept in sync by hand.
- Every Servarr behaviour the role handles — the config.xml round-trip, the auth-method
  trap, the unauthenticated-API assertion — is identical across the five. Copied five times,
  a fix lands in one and rots in four.
- The precedent already exists: `roles/observability` deploys two applications under one
  role because neither is useful alone.
- The user-facing rule is untouched. Each app has its own playbook, its own app-defaults
  file, its own Rundeck job and its own Semaphore template. One click per app.

`roles/_template-docker` remains the pattern for a genuinely new app. A sixth Servarr app is
a `vars/app-defaults/` file, a playbook and two job definitions.

## Facts the implementation depends on

- **The API key is declared, not discovered.** Servarr reads config overrides from
  `<APPNAME>__SECTION__KEY`, so the compose file sets the key before first start and the
  credential exists before the container that uses it. Both `<APP>__APIKEY` and
  `<APP>__AUTH__APIKEY` are set: the key moved sections across the v3→v4 line. **Neither is
  trusted.** The role reads `config.xml` back and exports whatever is actually there, so an
  ignored override costs nothing and a lab that already had the app keeps its existing key —
  which matters, because that key is what every Prowlarr Application and Bazarr connection
  in the lab was wired with.
- **A Servarr container opens its port before it has written config.xml.** The port is not a
  readiness signal; the file is. Same shape as the Uptime Kuma failure recorded in
  LESSONS.md, where four green deploys passed over an app sitting on its setup screen.
- **A v4 app with no authentication method serves its first-run setup page on every UI
  path** while answering normally from outside. Asserting `AuthenticationMethod` is present
  and not `None` is what distinguishes that state from a configured app.
- **`AuthenticationRequired: Enabled` is deliberate.** Under
  `DisabledForLocalAddresses` a call from the stack host succeeds without a key, so the
  unauthenticated-refusal assertion would pass on an app that is wide open to the LAN. The
  assertion is only meaningful under `Enabled`.
- **Prowlarr gets no media mount.** It manages indexers and never touches files.
- **Readarr pins `develop`.** There is no current stable Readarr tag.

## Unverified

Nothing here has run against a live Servarr. Both gates are green, which covers syntax and
lint and nothing about the vendor. The two vendor facts that matter are the env-var spelling
and the 401 behaviour, and both were written so that a wrong guess **fails the deploy
loudly** rather than shipping a wrong key or an open API — the config.xml readback replaces
a guessed key with the real one, and the unauthenticated call is an assert, not a debug.

Deploying Prowlarr and one *arr onto the lab's media_stack, then running Wire Media Stack,
exercises this slice and closes 504's last two open items at the same time.
