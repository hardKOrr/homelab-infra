# Work index

[GitHub Issues](https://github.com/hardKOrr/homelab-infra/issues) is the live work queue and
owns priority and status — see [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md). This table
maps each open issue to the slice that specifies it. Each linked slice owns its goal, current
state, and evidence. Historical narrative belongs in slice `notes.md` files or
[`INDEX-ARCHIVE.md`](INDEX-ARCHIVE.md); durable cross-cutting lessons belong in
[`LESSONS.md`](LESSONS.md).

## Start here

Open the linked issue, then read its slice. A `built` slice awaiting live observation is
tracked by an observation issue and does not displace an open implementation issue. Run both
gates before marking repository work built:

```text
bash gate/lint.sh
bash gate/test.sh
```

## Repository work

| Issue | Slice | Next outcome |
| --- | --- | --- |
| [#10](https://github.com/hardKOrr/homelab-infra/issues/10) | [507 — Stale service detection](507-stale-service-detection/README.md) | Detect and report services still running replaced code; restart them only through the maintenance schedule |
| [#11](https://github.com/hardKOrr/homelab-infra/issues/11) | [506 — Plex client troubleshooter](506-plex-client-troubleshooter/README.md) | Add read-only, evidence-ranked triage for Plex client log bundles |
| [#12](https://github.com/hardKOrr/homelab-infra/issues/12) | [408 — Application catalog](408-app-catalog/README.md) | Implement its application batches in the order defined by that slice |

The following option slices refine application-catalog rows and have no issue of their own
yet. Their own dependencies and acceptance criteria decide when each is actionable; file an
issue against one when it is ready to work:

| Subject | Slices |
| --- | --- |
| Website hosting | [409 — Publii](409-publii-static-site/README.md), [410 — WordPress](410-wordpress-site/README.md), [411 — Ghost](411-ghost-publication/README.md), [412 — Silex](412-silex-visual-builder/README.md) |
| CRM | [413 — Odoo](413-odoo-crm/README.md), [414 — SuiteCRM](414-suitecrm/README.md), [415 — Twenty](415-twenty-crm/README.md), [416 — EspoCRM](416-espocrm/README.md) |
| Email marketing | [417 — AcelleMail](417-acellemail-email-marketing/README.md) |

The following slice has no remaining repository implementation unless a Batch C
consumer's own acceptance exposes a defect:

| Issue | Slice | Remaining evidence |
| --- | --- | --- |
| [#47](https://github.com/hardKOrr/homelab-infra/issues/47) | [418 — Platform mail contract](418-platform-mail-contract/README.md) | A disposable-mailbox send test and credential-rotation test once a live provider account, sender domain and test mailbox are named; the first Batch C row that needs mail proving the contract end-to-end |

## Built; awaiting observation

These slices have no remaining repository implementation unless an observation exposes a
defect. Each has an open live-lab observation issue tracking the remaining evidence:

| Issue | Slice | Remaining evidence |
| --- | --- | --- |
| [#13](https://github.com/hardKOrr/homelab-infra/issues/13) | [205 — Maintenance schedules](205-maintenance-schedules/README.md) | Observe a real scheduled window and complete Tier 2 live-lab acceptance |
| [#14](https://github.com/hardKOrr/homelab-infra/issues/14) | [504 — Wire media stack](504-wire-media-stack/README.md) | The adoption path during a deliberately requested migration |

Completed and unreachable slices are retained under [`done/`](done/) and
[`no-target/`](no-target/). The slice format and lifecycle are defined in
[`README.md`](README.md).
