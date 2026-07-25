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

### Live acceptance TODO

- Fresh deploy: LXC created, web vault loads at wired domain, token printed once.
- Re-run: no changes, no token print, no service bounce.
- `-alpine` image extraction works for the then-current version (layer layout assumption).
- lab-* scripts behave per contract.
