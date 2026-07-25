# 400 — notes

## 2026-07-25 — implementation

### Deviation: no prebuilt binaries on GitHub releases

The README approach said "fetch latest binary from GitHub releases". Verified against the
live API: `dani-garcia/vaultwarden` releases carry **zero assets** (source archives only,
checked at 1.37.0). The container image is upstream's only binary channel.

Chosen mechanism (`roles/vaultwarden/files/vaultwarden-fetch`):
- `skopeo copy docker://vaultwarden/server:<ver>-alpine dir:...` then extract `vaultwarden`
  and `web-vault/` from the layer tars (manifest order, later layers win).
- The Alpine image is a **statically linked musl** binary (verified in
  `docker/Dockerfile.alpine`: blackdex rust-musl toolchain, alpine:3.24 final stage) — no
  runtime library deps on the Debian 12 LXC, and no separate `bw_web_builds` fetch needed
  since the image carries a version-matched web-vault.
- skopeo is a downloader only; no container runtime installed. Script sanity-runs
  `--version` on the extracted binary before installing.
- Version selection still via GitHub releases API (image tags match release tags), so
  `lab-update-check` and the deploy share one version source.

### Admin token flow (role tasks/main.yml)

Source order: `VAULTWARDEN_ADMIN_TOKEN` env var → `infrastructure.vaultwarden.admin_token`
→ hash already on the guest (continuity: a re-run before the user pastes the token does NOT
regenerate/reprint) → generate + print (first bootstrap only). Only the argon2id hash
touches the guest (`/etc/vaultwarden.env`, root:vaultwarden 0640).

Idempotency: argon2 salt is derived deterministically from the token (sha256 prefix) so the
PHC string — and therefore the env file — is stable across runs; a random salt would bounce
the service every deploy. Slightly weaker than a random salt, acceptable for a single
high-entropy 48-char machine token.

### Play 1 idempotency

Template PATH B as written would re-provision on every run (generate-ip skips taken IPs →
new IP → new VMID → duplicate guest). Playbook mirrors find-or-create-host.yml instead:
guest is tagged `<instance>`, re-run finds `tag_<instance>` group and reuses it. Worth
folding back into `_template.yml` PATH B when the pattern is proven live (not done here —
out of slice scope).

### Other

- `admin_port: 8080` removed from app-defaults — the admin panel is `/admin` on the main
  port; the key was fiction.
- Added `app.signups_allowed` (default false) as the one user-facing behavior knob.
- Port 80 as non-root: unit uses `AmbientCapabilities=CAP_NET_BIND_SERVICE`.
- `.gitattributes`: `ansible/roles/*/files/*` forced LF (scripts ship verbatim to guests).
- Gate status: lint clean (production profile); syntax-check clean for vaultwarden.yml.
  `test.sh` overall still fails on the pre-existing empty `stacks/rollback-container.yml`
  stub (slice 502, untouched).

## 2026-07-25 — aligned with the 401–406 pattern

Reviewed while building the rest of the 4XX tier. The implementation above stands;
two changes were made so all seven baseline apps behave identically.

- **Fact-writing moved into `apps/vaultwarden.yml`.** It used to live in a
  `Bootstrap | Record Vaultwarden facts` play inside `bootstrap.yml`, which meant a
  standalone `ansible-playbook apps/vaultwarden.yml` deployed the service without
  ever registering it. Every 401–406 playbook records its own registry key in Play 3
  before wiring; Vaultwarden now does the same, and the play was removed from
  `bootstrap.yml`. The two-pass admin token gate is unchanged.
- **Added a deploy notification** via the new shared `tasks/notify.yml`, per
  `.claude/specs/one-click-idempotent.md` ("every automated state change notifies").
  It is a silent no-op on the very first bootstrap pass, because Ntfy does not exist
  until step 2.

Gate re-verified after both changes: ansible-lint clean (production profile),
`apps/vaultwarden.yml` and `bootstrap.yml` syntax-check clean. The repo-wide
syntax failure is still only the empty `stacks/rollback-container.yml` stub
(slice 502, untouched).

Note for live acceptance: Vaultwarden now sits behind an **authenticated** Ntfy
(slice 401 closed the server by default), but nothing in this role publishes, so
nothing here changed. Its own admin-token flow is untouched.

### Live acceptance TODO

- Fresh deploy: LXC created, web vault loads at wired domain, token printed once.
- Re-run: no changes, no token print, no service bounce.
- `-alpine` image extraction works for the then-current version (layer layout assumption).
- lab-* scripts behave per contract.
