# Recovery acceptance protocol

This protocol gives every recovery method the same two destination transitions (a new
isolated target and an existing target) and the same failure boundaries, and defines where
a synthetic fixture's claims stop. It never claims a live application restore: live evidence
and application-specific assertions are recorded on the product's observation issue.

## Evidence boundary

A recovery validation record shared in a product issue contains method, declared
version label, a product-qualified fixture
artifact identity (while `artifact_id` preserves the native provider identity), target
identity/state and assertion names. It does not contain backup contents, keys,
credentials, endpoint secrets or live configuration. `restore_tested` in a report means
the synthetic fixture completed both routes; `live_verified` is a separate field and is
false until an authorized isolated lab run is recorded in the applicable product observation issue.

A validation record distinguishes three coverage sets:

- **implemented** — a method is declared and its adapter interface is present;
- **fixture-tested** — the common protocol passed for that method and destination;
- **live-verified** — an operator-recorded run on an authorized isolated target (empty in
  this checkout; a green gate never adds an item here).

Products with no declared recovery adapter can be identified from
`catalog/applications.yml`. That disposition is not a priority list. Must-keep ordering
is supplied by the user and is never inferred from catalog, file, or issue order.

## Common protocol

Use this protocol when validating the shared PBS VM and LXC routes and declared
application recovery methods:

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

A synthetic fixture is not live restore evidence. The gate runs lint, syntax checks and
five logic suites as described in [`gate/README.md`](../gate/README.md); it does not
execute this recovery protocol.

## Repository rollup

The current declarations name the native method for every product that exposes
`recovery.methods: [native]`, plus the shared `pbs_guest` VM and LXC routes. No
`project_managed` method is declared. Declarations alone do not establish fixture or
live verification. A product's observation issue should attach its validation record and
add the installed product version, artifact identity and application-specific assertions
without adding secrets or backup contents. Product-specific method decisions and failure
matrices are in the recovery specifications listed in [`specs/README.md`](specs/README.md).

## Fixture lifecycle and live work

The fixture protocol ends after verification and evidence creation. It does not authorize
production cutover, source retirement, or automatic cleanup. A live rehearsal requires a
separately authorized isolated source/destination, exact affected scope and data handling,
and a recorded retry/recovery action for a failed target. Leave the final target identity
and state in the applicable product observation issue; handle cleanup as a separately scoped operation.
