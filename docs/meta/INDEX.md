# Work index

This is the repository work queue. Each linked slice owns its goal, current state, and evidence.
Historical narrative belongs in slice `notes.md` files or
[`INDEX-ARCHIVE.md`](INDEX-ARCHIVE.md); durable cross-cutting lessons belong in
[`LESSONS.md`](LESSONS.md).

## Start here

Take the first actionable row. A `built` slice awaiting live observation is not repository work
and does not displace an open implementation row. Run both gates before marking repository work
built:

```text
wsl bash -lc 'bash gate/lint.sh'
wsl bash -lc 'bash gate/test.sh'
```

## Repository work

| Order | Slice | Next outcome |
| ---: | --- | --- |
| 1 | [507 — Stale service detection](507-stale-service-detection/README.md) | Detect and report services still running replaced code; restart them only through the maintenance schedule |
| 2 | [506 — Plex client troubleshooter](506-plex-client-troubleshooter/README.md) | Add read-only, evidence-ranked triage for Plex client log bundles |
| 3 | [408 — Application catalog](408-app-catalog/README.md) | Implement its application batches in the order defined by that slice |

The following option slices refine application-catalog rows. Their own dependencies and
acceptance criteria decide when each is actionable:

| Subject | Slices |
| --- | --- |
| Website hosting | [409 — Publii](409-publii-static-site/README.md), [410 — WordPress](410-wordpress-site/README.md), [411 — Ghost](411-ghost-publication/README.md), [412 — Silex](412-silex-visual-builder/README.md) |
| CRM | [413 — Odoo](413-odoo-crm/README.md), [414 — SuiteCRM](414-suitecrm/README.md), [415 — Twenty](415-twenty-crm/README.md), [416 — EspoCRM](416-espocrm/README.md) |
| Email marketing | [417 — AcelleMail](417-acellemail-email-marketing/README.md) |

## Built; awaiting observation

These slices have no remaining repository implementation unless an observation exposes a defect:

| Slice | Remaining evidence |
| --- | --- |
| [205 — Maintenance schedules](205-maintenance-schedules/README.md) | Observe a real scheduled window and complete Tier 2 live-lab acceptance |
| [504 — Wire media stack](504-wire-media-stack/README.md) | The adoption path during a deliberately requested migration |

Completed and unreachable slices are retained under [`done/`](done/) and
[`no-target/`](no-target/). The slice format and lifecycle are defined in
[`README.md`](README.md).
