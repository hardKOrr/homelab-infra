# Recovery drill and data port

Live procedures for one application instance. How live runs and fixes work is in
[Live operations](../../README.md#live-operations).

## Application recovery drill

The drill proves deploy, convergence and native recovery for one instance. Running it
authorizes every step below on the issue's target instance, including Remove with
`delete_data=true` and overwrite Restore. Go straight through. A lab-owned resource in the
way, such as another instance of the same application, a shared backend or a failed run's
leftovers, is yours to remove, redeploy or reconfigure; redeploy any instance you removed
once the drill ends. Stop only for a guest without the `_+lab` tag, a safety guard that
trips, or a failure the issue's scope cannot fix. Read
inputs from the playbook headers and the application's documentation; Rundeck options map
to the same inputs.

1. Verify the target carries the lab tag, its dependencies exist (deploy them if absent),
   and the candidate checkout is at the intended revision. Choose a few checks that show
   other applications and the platform are unaffected.
2. Deploy. Verify HTTPS and login with the canonical credential, and that the declared
   DNS, ingress, identity provider and monitor wiring each appear exactly once.
3. Create the issue's data and configuration canaries through the application's normal
   interface and read both back. Deploy again; both survive and the wiring stays unique.
4. Back up through the application's backup playbook. Record the recovery point and
   verify it exists.
5. Remove with `delete_data=true`. For a drill that requires a fresh native guest, add
   `destroy_guest=true`; this is limited to one `_+lab` LXC or VM carrying the exact
   instance tag and rechecks its live tags before destruction. Verify the instance, its
   data and wiring are gone, the backup remains, and other applications are unaffected.
   Without that explicit option, Remove retains the guest for redeployment.
6. Deploy a fresh instance with the same name. Verify it works and neither canary is
   present. An overwrite Restore of an existing target requires `pre_restore_point`;
   back up the fresh instance with Backup App, verify that point exists, and pass it as
   `pre_restore_point`. Run Restore with `overwrite=false` to preview, then restore the
   recovery point recorded in step 4 with overwrite.
7. Read both canaries back exactly, verify login and unique wiring, then Deploy once more.
   Restored data, configuration and the other applications remain.

After the fresh Deploy in step 6, an SSO-protected route can return 404 for up to about
five minutes while the Authentik embedded outpost reconnects. Wait and retry the same
check in steps 6/7; restart nothing.

A check that cannot be read back counts as unverified.

For a stateless application, skip backup and restore and prove a fresh deploy recreates
its configuration and wiring. For multiple instances, verify siblings stay intact. If the
application has no backup and restore yet, build them in the repository before step 4.
For those new implementations, `restore.yml` replaces state through the shared
[`tasks/recovery/restore-swap.yml`](../../tasks/recovery/restore-swap.yml), which moves the
original aside on the same filesystem and puts it back on any later failure, as
[`roles/tautulli/tasks/restore.yml`](../../roles/tautulli/tasks/restore.yml) does. App-specific
checks go in its hooks, with a gate test that runs them.
Restoring a whole stack guest does not prove one application's restore. Per-app recovery
behavior is in each role's README, listed under Areas in
[`ansible/README.md`](../../README.md#areas).

After the drill, when no PR is needed, post the result comment and close the issue.

## Application data port

The issue names the source, destination, capture method, required path or address
changes, and the counts to compare. Source access comes from the operator's verified
private values. The source is not tagged `_+lab`, so it is read-only: capture from it, and
never stop, change or delete it. If it has no consistent read-only export, report that as
the blocker.

Capture only application data, through a consistent export or backup, and keep the copy
private. Change only the paths, peer addresses and public URL the destination needs.
Import through the repository's Restore or Import path into a fresh instance; if that
path is missing, build it in the repository.

Verify the issue's counts and spot checks, a real account's login and unique wiring. Back
up the destination through homelab-infra, record its first recovery point, and verify a
final Deploy keeps the imported data.
