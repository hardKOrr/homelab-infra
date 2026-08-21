# Notes — 414 SuiteCRM

## 2026-08-20 — Why this option exists

SuiteCRM is the conventional full-suite CRM option. Its upstream module set covers accounts,
contacts, leads, opportunities, campaigns, cases, reports, workflows and administrator-defined
fields and layouts. This makes it the strongest candidate when breadth and mature CRM concepts
matter more than a minimal interface.

SuiteCRM 8's production documentation starts from a pre-built installation package on Apache,
not from a first-party production container contract. The hosting kind therefore remains open
until implementation proves either a native LXC installation or a maintained upstream-supported
image. Current upstream requirements include MariaDB or MySQL, a scheduler cron job, and, from
SuiteCRM 8.10, a Symfony Messenger worker for asynchronous tasks.

MariaDB is the proposed default because it already belongs to slice 408's backend contract. The
role must pin a compatible PHP, Apache, SuiteCRM and MariaDB combination rather than follow each
component's latest tag independently.
