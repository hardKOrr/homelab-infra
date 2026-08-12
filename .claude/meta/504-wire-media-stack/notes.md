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

## The wiring ran for real, 2026-08-09 — execution 72

Four apps resolved (lidarr, prowlarr, radarr, sonarr) and three Prowlarr Applications
created: `lidarr → prowlarr indexers`, `radarr → …`, `sonarr → …`. No download client is
deployed by the platform on this lab, so that half of the wiring is still unexercised, and
Bazarr with it.

**It ran green with nothing to do first, and that is the finding.** Execution 67 reported
`Media stack: 0 app(s) resolved (none)`, wired nothing and exited 0, after four successful
deploys. The cause was in the deploys, not here — Ansible does not template a YAML mapping
key, so every app had written its registry entry under the literal key `{{ instance }}` and
overwritten the app before it (fixed in `86de49e`). But this playbook treats an empty
registry as a normal outcome, so the only signal was one `msg:` in a passing log. A stack
that resolves zero apps when the registry is non-empty is worth failing on.

## Acceptance caught up with the lab, 2026-08-12

No code changed. Three criteria had been satisfied by live runs and never ticked, which is
how a slice stays "live" long after its work is done.

- Plays 1–3 have run end to end three times (72, 79, 90). Play 2's SSH read of each *arr's
  `config.xml` is not an untested leg — it is the only reason Play 3 has API keys.
- The Ntfy notification is proven **by the shape of a green run**, not by someone watching a
  phone: since `44dd7c3` an undelivered publish is fatal, and execution 90 was green.
- What is left is the adoption PUT, and it cannot be manufactured — it needs a record this
  platform did not create. `Migrate Servarr` against the operator's old Radarrs is the one
  run that produces it, and it is also 505's last item. **One click closes two slices.**
