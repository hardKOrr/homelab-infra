# Notes — 410 WordPress website hosting

## 2026-08-20 — Scope

WordPress remains the broadest option, but it is not first in implementation order. It consumes
the database-provisioning contract that slice 408 assigns to Batch B. Do not hide MariaDB inside
the WordPress compose project.

The wife-facing default is a maintained block theme and the Site Editor. Claude is useful for
CSS, block patterns and narrowly scoped custom behavior. Generated code must not become a reason
to install an unreviewed plugin or to grant another account administrator access.

Backups must cover both halves of the site at one recoverable point: database state and uploads.
A filesystem-only archive is not a WordPress backup.
