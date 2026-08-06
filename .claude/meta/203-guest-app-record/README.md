# 203 — Record deployed apps on the Proxmox guest

**Status:** done
**Depends on:** none
**Blocks:** none

## Problem

Nothing after guest creation ever wrote a guest's tags or notes. A stack host running twelve
apps and one running a single app were indistinguishable in the Proxmox UI: the only record
of what lived where was `config/apps/<instance>.yml` on the control node and `docker ps` at
runtime. An operator looking at the guest tree could not answer "what does this box run?",
and neither could a dynamic inventory.

The creation-time tag is guest **identity**, not tenancy — `ntfy` for a native app's own LXC
(`playbooks/apps/ntfy.yml:103`), `stack_name` for a shared Docker host
(`tasks/stack/find-or-create-host.yml:163`). It is written once and never revisited, so it
says what the guest *is* and can say nothing about what was later deployed onto it. It also
carried install type only by convention: a hostname ending in `-stack` meant Docker, which is
a naming convention doing a data model's job and fails the moment a host is renamed or a
native app lands on a shared guest.

## Files

- `ansible/files/proxmox/guest-app-record.py` — the merge. Read-modify-write against
  `pvesh .../config`, keyed by instance
- `ansible/tasks/proxmox/record-app-on-guest.yml` — the deploy-side include, best-effort and
  never fatal
- `ansible/tasks/unwiring/guest-record.yml` — the withdrawal
- `ansible/playbooks/apps/{vaultwarden,ntfy,caddy,authentik,uptime-kuma,observability,pbs}.yml`
  and `_template.yml` — one include each, last in the Wire play
- `ansible/playbooks/apps/remove.yml` — withdraws after it locates the host, reusing
  `_rm_hosts`, the only point in the play that already distinguishes a shared stack host from
  a native app's own LXC

## Approach

- Stamp two tag families and one notes row per deployed instance:
  - `app_<instance>` — tenancy; also yields a `tag_app_<instance>` inventory group
  - `kind_<docker|native>` — install type; yields `tag_kind_docker`
  - a row in a marker-delimited region of the guest's notes: instance, kind, url, deployed
- Render **both** tag families from the merged notes table rather than splicing them into the
  live tag field. The kind tag answers a question about the guest, not about one app: two
  Docker apps on a stack host share one `kind_docker`, and withdrawing one must leave it
  standing. One parse, one merge, two renderings that cannot disagree.
- Leave every unmanaged tag alone — the identity tag, a lab-wide tag from
  `config/proxmox.yml`, anything hand-applied.
- Read and write through `pvesh` on the node, not `pct`: `pct config` percent-encodes the
  description onto one line and PVE 9.2 rejects `--output-format`, so the decoded text is
  only available from `pvesh get /nodes/<node>/<type>/<vmid>/config --output-format json`.
  The write uses the same endpoint, so one code path serves `lxc` and `qemu`.
- Resolve guest identity by hostname through `/cluster/resources`, not through inventory
  hostvars: the `deploy_<instance>` member is an `add_host` entry, and a guest created earlier
  in the same run carries no `proxmox_vmid` or `proxmox_node` at all — exactly the
  first-deploy path that most needs recording.
- Keep it best-effort. A vanished guest or a refused update reports on stdout and does not
  fail an otherwise successful deploy. This is bookkeeping, not the deploy.

### Idempotency rules

Two rules exist to keep a re-deploy a genuine no-op, and both were load-bearing in practice:

- An existing row keeps its recorded date when nothing else about it changed, so `deployed`
  reads as "first written or last changed" rather than "when the playbook last ran". Without
  it every re-run rewrites the date and no deploy ever reports `changed: false`.
- Both sides of the tag comparison normalise through `tag_list()`, because clearing a guest's
  last tag leaves PVE reporting the field as a single space rather than an empty string, which
  would otherwise differ forever and rewrite the config on every run.

## Acceptance

- [x] A native app's deploy stamps its own LXC — **ntfy, execution 15**: tags became
      `app_ntfy;homelab-infra;ntfy`, the marker region appended below the pre-existing
      `LXC Container created by Ansible`, URL derived from the wiring contract, kind `native`
- [x] A second deploy of an unchanged app is a no-op — **ntfy, execution 16**: the
      `Merge this instance into the guest's tags and notes` task reported `ok`, not `changed`,
      and the recorded date did not move
- [x] Every baseline app playbook stamps, including Vaultwarden — **execution 17**, after the
      omission below was fixed
- [x] A Docker app on a shared stack host records `kind_docker` — **authentik, execution 19**:
      `app_authentik;homelab-infra;kind_docker;sso_stack`, identity tag untouched
- [x] An already-recorded guest gains the kind tag without churning its row —
      **vaultwarden, execution 20**: tags gained `kind_native`, the row's `deployed` date
      unchanged
- [x] Both gates green

Covered by the twelve merge cases rather than by the live lab, which has no such guest yet:
a mixed-kind host, sibling withdrawal leaving `kind_docker` standing, last-app teardown
removing both families and the region itself, a lab-wide tag left alone, PVE's single-space
empty tag field, an instance name outside PVE's tag alphabet, and operator text below the
end marker.

## Notes

**The feature shipped missing one playbook.** `183cb13` added the include to every app
playbook it could find and missed `vaultwarden.yml` — the one that matters most to miss, since
Vaultwarden is first in bootstrap order, so the estate's founding guest was the only one that
would never be stamped while `remove.yml` withdrew unconditionally and would have gone looking
for a row nothing had written. Fixed in `2e07ffb`. The gates cannot see a missing include;
only reading the seven playbooks side by side found it.

**Live withdrawal is unobserved.** `remove.yml`'s withdrawal path has not run against the
lab — removing a baseline service to prove it is a larger decision than this slice. The
withdrawal logic is covered offline (cases 6–8, 12). Slice 501 owns the removal run and is
where that evidence belongs.

**The kind vocabulary is two values** (`docker`, `native`, constrained by argparse) and
`native` means a systemd unit today. The tag renders from the value, so widening the
vocabulary — `systemd`, `binary`, a VM's own installer — is a one-line change here plus
whatever decides the value in `record-app-on-guest.yml`.

**Guests deployed before this exists stay unstamped until their next deploy.** As of
2026-08-05 that is `caddy` (168000010), `monitoring-stack` (168000014) and `pbs` (168000015).
Re-running each app's deploy job stamps it; nothing backfills.
