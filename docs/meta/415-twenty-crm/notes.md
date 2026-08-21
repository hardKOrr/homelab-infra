# Notes — 415 Twenty CRM

## 2026-08-20 — Why this option exists

Twenty is the modern-interface option in this group. It emphasizes configurable objects,
workflows and integrations rather than the larger fixed module inventory of SuiteCRM or Odoo.
Upstream describes Docker Compose as the supported self-host path and requires HTTPS for some
browser features outside localhost.

The upstream deployment includes application server and worker processes, PostgreSQL, Redis and
persistent server storage. This repository does not accept hidden database sidecars, so the role
must replace the example PostgreSQL and Redis services with named Batch B backend instances while
preserving the upstream server/worker contract.

Twenty can execute workflow logic when an execution mode is enabled. That is a separate security
decision from deploying CRM. The default must keep logic execution disabled, which matches
upstream production behavior, until the operator explicitly selects and configures a mode.
