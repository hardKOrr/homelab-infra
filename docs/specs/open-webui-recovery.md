# Open WebUI recovery

This is the product-specific recovery contract for [Open WebUI observation issue #225](https://github.com/hardKOrr/homelab-infra/issues/225) and [closed implementation issue #185](https://github.com/hardKOrr/homelab-infra/issues/185). Repository implementation and synthetic fixtures are evidence for #185; they do not claim a live backup, restore or production cutover.

## Method decision

| Method | Disposition | Recovery unit |
|---|---|---|
| `native` | **supported** through `Backup App` / `Restore App` | One application-consistent PBS point containing the complete `/opt/<instance>/data` directory as `data.pxar`; the generated `WEBUI_SECRET_KEY` and named Ollama/LiteLLM connections are separate required recovery material. Upstream model data is not part of this point. |
| `pbs_guest` | **supported only as a shared whole-guest fallback** through `Backup Guest` / `Restore Guest` | The shared `ai` Docker guest (the tracked stack currently resolves to an LXC) and every workload on it; never Open WebUI alone. Other guests, external mounts, backend services and their state need separate evidence. |
| `project_managed` | **unsupported / not declared** | No Open WebUI project-managed restore task exists. |
| rebuild-only | **not sufficient** | Redeploying the container does not recover users, chats, configuration, uploads or vector data. Rebuild is a prerequisite for a new target, not a recovery method. |

The tracked default in `ansible/vars/app-defaults/open-webui.yml` is `ghcr.io/open-webui/open-webui:main`. `main` is a moving tag, not a product version or immutable image identity. Before live evidence, record the source's installed version and image digest, and use a compatible target. The role does not embed a version marker in its product data directory, so the PBS path alone cannot prove source/target version compatibility. A version mismatch or unknown version must remain unverified rather than healthy.

Open WebUI's [backup guidance](https://docs.openwebui.com/tutorials/maintenance/backups/) describes file-level backup of the complete persistent data store and stopping the container first for SQLite consistency. Its [update guidance](https://docs.openwebui.com/getting-started/updating/) likewise backs up the persistent volume before restore. The role follows that data boundary using PBS's native `pxar` member; it does not invent a second Open WebUI archive format.

## Exact recovery unit and independent prerequisites

For instance `<instance>`, the native artifact is the complete configured `app.data_path` (default `/opt/<instance>/data`), mounted as `/app/backend/data`. With the tracked SQLite default, that includes `webui.db`, users, chats, settings, uploaded files, local vector data, cache and other files owned by that directory. Backup stops only Open WebUI, requires a non-empty database with `PRAGMA integrity_check` returning `ok`, then uploads one `data.pxar` member to PBS group `host/<backup_id>` (default `host/<instance>`). Restore reads and checks the selected PBS member and its SQLite database before replacing the target directory, then requires the target `/health` endpoint.

The generated `WEBUI_SECRET_KEY` lives in the hidden `secret_key` field of Vaultwarden item `homelab-infra/apps/<instance>`; it is never stored in the PBS artifact or recovery evidence. It must be readable for both destinations. For a new target, restore copies the source key into the target's Vaultwarden item and root-only environment file before starting restored state, so data protected by the original key remains usable. A missing/unreadable source item or a failed Vaultwarden update prevents a healthy result.

| State / dependency | Disposition |
|---|---|
| SQLite database, accounts, chats, saved settings and app-managed data | Captured with all files under `app.data_path`; offline `webui.db` integrity is checked on capture and restore. |
| Uploaded documents, local vector data and cache | Captured with the complete directory. A custom path or mount outside the configured directory is not included unless explicitly redirected into that path. |
| Generated application key | Required independently from the source Vaultwarden item; transferred securely to a new target before service start. |
| Named upstream connections | The source/target instance config must explicitly select deployed Ollama and/or LiteLLM names in `app.upstreams`. Deployment validates provider identity and a registered endpoint; restore never guesses a replacement backend. The target's endpoint and any required API credential are independently required. |
| Upstream model files and model-service state | Separate from Open WebUI and recovered through the owning Ollama/LiteLLM recovery path. The native Open WebUI point does not copy, stop or remove those services or their data. |
| Application/login credential for acceptance | Accounts and password hashes are in the database; an authorized operator must independently have a test login and verify it after restore. No login value goes into evidence. |
| PBS datastore, repository, fingerprint, API token and configured decryption material | Required external recovery inputs. Missing or unreadable access is failure, not a healthy/empty inventory. Site-wide PBS retention is independent; Open WebUI restore does not prune snapshots. |
| Docker image, Compose definition and Open WebUI version | Rebuild input from tracked defaults and authored target config. The current `main` tag is mutable; record the installed source version/digest before live recovery and verify target compatibility. |
| CPU, RAM, address/port, target storage and free target path | Target prerequisites. New destination requires a distinct authored config, target-owned path and route identity. |
| Reverse proxy, DNS, SSO/catalog, monitoring, notification and other external services | Not in the data point. New restore remains isolated until app verification, then intended target wiring may be reconciled; no implicit cutover is performed. |
| GPU, hardware passthrough, external mounts or external database | No dedicated Open WebUI device or external database is declared in the tracked defaults. Any operator override must be inventoried and verified separately; this method does not claim to restore it. |

The installed recurring timer and `Backup App` action call the same root-owned helper. The tracked schedule is `0 3 * * *` in the AI guest's local timezone and is installed only when backup is enabled and PBS is usable. Its root-only config contains PBS access material. If PBS is absent, deployment does not claim a recurring backup. The helper uses a per-instance lock, stops only Open WebUI, validates SQLite, pushes `data.pxar`, and restarts the service after a failed attempt where possible. It does not prune PBS points; datastore-wide retention remains an independent policy to verify.

This checkout has no authorized live source, installed-version observation, schedule firing or PBS artifact. Schedule status, artifact identity/age, PBS integrity, key/credential readability, external upstream availability and live consistency are therefore **unknown/deferred**, not healthy by declaration. A live record on observation issue #225 must independently identify the authorized source and isolated destination; installed version/digest; recurring timer observation; full `host/<backup_id>/<timestamp>` identity and age; PBS verification result and `data.pxar` readability; source key; target storage; named upstream endpoints/credentials; SQLite integrity; login, chat, upload and vector checks; and both destination results. Do not attach backup contents, endpoint secrets or credentials.

## Destination protocol

### New isolated instance

1. Author a distinct target config with target-owned storage and a distinct route. Select only already-deployed, explicitly named Ollama/LiteLLM endpoints with target-appropriate credentials. Keep the target's path outside all source-owned storage.
2. Run `Restore App` in plan mode with `destination=new`, source `instance`, exact target and selected A recovery point. Confirm the provider identity and collision plan; plan mode provisions or changes nothing.
3. Run with `overwrite=true`. Shared orchestration provisions the target through the normal app playbook with `recovery_isolated=true`; wiring, route publication and recurring timer are withheld. The source app is not contacted.
4. Before replacing target data, restore A's `data.pxar` to private staging and require readable SQLite integrity. Carry the source `WEBUI_SECRET_KEY` into the target Vaultwarden item and environment file, then replace only the target's `app.data_path`, start only target Open WebUI and require `/health`.
5. Verify target identity and route isolation, source-independent operation, login, representative chat/document/vector access, restored app-owned connections and target named upstreams. Reconcile intended target wiring only after those checks. This is not a cutover.

Missing target config, identity/path collision, wrong source backup group, missing/unreadable PBS credentials or source key, unreadable/corrupt/incomplete `data.pxar`, missing `webui.db`, failed SQLite integrity or incompatible application version blocks healthy recovery. A failed new target remains stopped or isolated; retry after fixing the target or point. No source, source point or unrelated resource is cleaned up.

### Existing instance

Use the exact managed target and plan mode first. Before destructive restore, take and independently retain a B point for that exact target. Pass it as `pre_restore_point`; it must differ from A and belong to the target's configured PBS backup group. Restore App reads B's full `data.pxar` into private staging and checks the contained SQLite database before stopping the target. If B is absent, unreadable, incomplete, corrupt or incompatible, the current target remains unchanged and replacement is refused.

Then restore selected A. The acceptance transition is **A → B → restore A**: preserve an independent B point, verify A's representative chats/uploads/settings, prove B-only state is gone, and confirm that retained B still restores for retry. Do not use A as the target's pre-restore point. If replacement is interrupted after the target is stopped or data is changed, leave Open WebUI stopped, recover B from the retained point and verify it before retrying A. The lock is released by the task's cleanup path; investigate a stale lock rather than deleting it blindly.

Neither destination stops the source, publishes an isolated target route early, prunes a recovery point, performs production cutover or removes unrelated resources. Temporary staging is removed after the attempt; backup data and retained B remain intact.

## Failure and retry matrix

| Case | Expected result |
|---|---|
| Wrong A backup group, wrong target B group or A reused as B | Refuse before target replacement; retain source and target points. |
| Missing/unreadable PBS token, fingerprint, datastore, decryption material or target Vaultwarden item | Refuse/fail visibly; never report healthy. |
| Missing/incomplete/corrupt `data.pxar`, missing `webui.db`, unreadable SQLite or integrity result other than `ok` | Refuse before replacement; keep target as-is if no mutation began. |
| Existing destination has no independent readable B point | Refuse before stopping/replacing the existing target. |
| Missing source application key or failed target Vaultwarden write | Refuse before data replacement or leave changed target stopped; repair key and retry. |
| Source and target image/version compatibility unknown or incompatible | Do not accept as verified; use a compatible image and repeat the isolated fixture/live validation. |
| Selected upstream name absent, provider mismatched, endpoint unreadable or required credential missing | Recovery is incomplete even if Open WebUI health passes; repair target config/dependency and retry verification. Model data remains separately owned. |
| Backup/restore overlaps or a stale lock remains | Refuse overlapping operation; inspect the owner before clearing a stale lock and retrying. |
| Failure after data replacement or service start/health | Keep target stopped or isolated; restore verified B when existing, then retry A. |
| Shared `ai` guest restore | Report every workload on the affected guest. Never describe the whole-guest path as an Open WebUI-only restore. |

`gate/test-open-webui-recovery.py` exercises both native destinations, source isolation, chats/uploads, key handling, named upstream boundaries, A → B → restore A, B recovery/retry and the shared wrong-target/missing-key/corrupt/version/external-data/partial-recovery matrix. `gate/test-open-webui-contract.sh` checks the role boundary and removal safety. The common report keeps schedule, artifact, integrity, external-data, credential and live restore states unknown unless independently supplied.

Repository and fixture verification are the closure scope for #185. Authorized live acceptance is deferred to [observation issue #225](https://github.com/hardKOrr/homelab-infra/issues/225), with evidence recorded on that issue. No live source or destination was changed for this slice.
