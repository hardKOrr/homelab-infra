# Notes — 411 Ghost publication hosting

## 2026-08-20 — Scope

Ghost has the strongest authoring experience of these options for a publication. Memberships and
newsletters are native rather than assembled from plugins. Claude is useful for Handlebars theme
changes, while ordinary posts remain visual-editor work.

Ghost 6 supports MySQL 8 as its production database and explicitly does not support MariaDB.
This adds a MySQL backend row to Batch B; a private database inside Ghost would violate the
catalog's one-click, independently backed-up backend contract.

Transactional mail is required for production. Self-hosted bulk newsletters currently require
Mailgun, so the implementation must keep transactional SMTP and optional bulk delivery distinct.
Do not imply that the platform's future generic SMTP relay automatically enables newsletters.
