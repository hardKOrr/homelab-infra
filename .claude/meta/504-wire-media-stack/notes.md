# 504 — build notes

## Live verification (2026-07-25)

Verified read-only against the operator's live media apps: Prowlarr 192.168.1.8,
Sonarr 4.0.19 (.12, .13), Radarr 6.2.1 (.14, .15), Lidarr 2.12 **plugins branch** (.20),
Bazarr 1.6.0 (.19), SABnzbd (.72), Deemix (.70), Slskd (.71).

Method: the three task files were copied with every write task mechanically replaced by a
`WOULD WRITE` debug (script kept out of the repo — it lives in the session scratchpad), then
driven by a harness that mirrors Play 3. No live record was created, updated or deleted.

| Run | Registry | Result |
|---|---|---|
| Idempotency | mirrors the live lab | 12/12 `confirmed`, zero would-writes, zero rescues |
| Drift — apps | wrong `baseUrl`, unregistered instance name | `updated`, `created` — both would-writes fired |
| Drift — clients | wrong category, unregistered client name | `updated`, `created` |
| Drift — Bazarr | pointed at a different Sonarr | `updated` |
| Failure path | *arr host unreachable | recorded as `failed`, run continued, `rescued=2` |
| Empty stack | `media_registry: {}` | every task skipped, no failure |

Both gates green: ansible-lint production profile on 143 files, `--syntax-check` on every
playbook.

## Facts the implementation depends on

- **API versions differ.** Sonarr/Radarr are `/api/v3`; Lidarr/Readarr/Prowlarr are
  `/api/v1`. Held in `media_wiring.kinds.<kind>.api`.
- **Secrets read back masked.** *arr apps return `apiKey`/`password` as `********` on GET, so
  those fields are excluded from drift comparison — including them would rewrite every
  record on every run. Bazarr is the exception: it returns peer API keys in clear, so its
  key *is* compared.
- **Payloads start from the app's own `/schema`.** Settings this wiring does not own
  (priorities, `removeCompletedDownloads`, content layout, Prowlarr sync categories) keep
  whatever the app or the operator set. Only fields the schema declares are sent, which is
  why category-less clients (Deemix, Slskd) work through the same code path as SABnzbd.
- **Deemix and Slskd are Lidarr plugins-branch clients.** Mainline Lidarr has no such
  implementation; the assert reports that clearly rather than posting an unusable client.
- **Bazarr is not an *arr.** One settings document, form-encoded `settings-<section>-<key>`
  writes, `X-API-KEY` header, and exactly one Sonarr and one Radarr — hence `peers` (or the
  first app of each kind) rather than a full product.

## Open

- Nothing on the *arr side wires media apps into the platform automatically, because no
  media app role exists yet. When one lands it should write `media.<instance>` via
  `write-generated-facts.yml`; until then the discovery path (`app.media_kind` in an
  instance file) is the supported route and is what a lab with pre-existing apps uses.
- qBittorrent is implemented from its schema but was not exercised live — the operator's
  qBittorrent instances are all disabled.
- `unwiring/` has no media counterpart: removing an app leaves its Prowlarr Application and
  download-client entries behind. Worth a slice if it bites.
