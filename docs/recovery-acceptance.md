# Recovery acceptance protocol

This is the repository and fixture side of the [repeatable recovery proof job #206](https://github.com/hardKOrr/homelab-infra/issues/206).
It does not claim a live application restore. The current per-product observation issue owns the live evidence and application-specific
assertions; this protocol gives each method the same two destination transitions and the
same failure boundaries.

## Evidence boundary

The report produced by `python3 gate/recovery_acceptance.py` is safe to paste into a
product issue. It contains method, declared version label, a product-qualified fixture
artifact identity (while `artifact_id` preserves the native provider identity), target
identity/state and assertion names. It does not contain backup contents, keys,
credentials, endpoint secrets or live configuration. `restore_tested` in a report means
the synthetic fixture completed both routes; `live_verified` is a separate field and is
false until an authorized isolated lab run is recorded in the applicable product observation issue.

The report has three deliberately separate coverage sets:

- **implemented** — a method is declared and its adapter interface is present;
- **fixture-tested** — the common protocol passed for that method and destination;
- **live-verified** — an operator-recorded run on an authorized isolated target (empty in
  this checkout; a green gate never adds an item here).

`catalog_remaining` is generated from `catalog/applications.yml` and names products with
no declared recovery adapter yet. It is a disposition, not a priority list. Must-keep
ordering is supplied by the user and is never inferred from catalog, file, or issue order.

## Common protocol

`gate/test-recovery-acceptance.py` runs this protocol for the shared PBS VM and LXC
fixtures and every currently declared application method:

1. Capture representative A state and a named recovery point without recording its data.
2. For **new**, make the target distinct and isolated, make the source unreachable, and
   restore A without reading or mutating the source. Verify records/files, target-owned
   identity and target-specific connections. A failed attempt leaves the target inactive
   and a retry is deterministic.
3. For **existing**, capture A, mutate the disposable target to B, retain an independent
   B pre-restore point, restore A, and verify B-only state is gone while B remains usable.
   Interrupt after replacement, verify the target is inactive and not complete, recover B
   from the retained point, then retry A.
4. Exercise the negative matrix before a target can report complete recovery: excluded
   external data, missing keys, incompatible versions, wrong target/kind, corrupt
   artifacts, source-owned storage, identity/route collision, incomplete shared-guest
   scope and partial recovery.
5. Assert that production cutover, source changes, unrelated-resource cleanup and
   production side effects did not occur. The target's final identity/state is part of the
   evidence record.

The fixture is intentionally not a second provider or application test platform. The
PBS VM/LXC implementation remains covered by `test-guest-recovery-contract.py`; product
roles keep their focused contract tests and application-level assertions beside the
changed seam. Container and Kind lanes remain the disposable hosting harnesses described
in [`gate/README.md`](../gate/README.md); neither is promoted to restore evidence by this
protocol.

## Current repository rollup

The current declarations exercise the native method for every product that exposes
`recovery.methods: [native]`, plus the shared `pbs_guest` VM and LXC routes. No
`project_managed` method is declared. The generated report is authoritative for the
exact set and for the remaining catalog disposition; it intentionally reports live
coverage as deferred. Product issue records should attach their generated evidence and
add the installed product version, artifact identity and application-specific assertions
without adding secrets or backup contents. Open WebUI's method decision and fixture
record are detailed in [`docs/specs/open-webui-recovery.md`](specs/open-webui-recovery.md),
which remains the repository-side link for the [Open WebUI observation issue #225](https://github.com/hardKOrr/homelab-infra/issues/225).

## Fixture lifecycle and live work

The fixture protocol ends after verification and evidence creation. It does not authorize
production cutover, source retirement, or automatic cleanup. A live rehearsal requires a
separately authorized isolated source/destination, exact affected scope and data handling,
and a recorded retry/recovery action for a failed target. Leave the final target identity
and state in the applicable product observation issue; handle cleanup as a separately scoped operation.
