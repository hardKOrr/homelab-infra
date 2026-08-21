# Notes — 416 EspoCRM

## 2026-08-20 — Why this option exists

EspoCRM is the focused conventional option. It covers the core account, contact, lead and
opportunity model without making the deployment an ERP suite. Its official Docker documentation
provides the clearest production container contract of the traditional CRM candidates.

The upstream Compose shape contains MariaDB, the web application, a daemon process and an
optional WebSocket process. Application data and customizations occupy multiple persistent
paths. The platform role must consume a separately deployed MariaDB instance and must recover
the database together with every persistent path; a database-only backup is incomplete.

The official image accepts supported credentials through Docker secret files. That matches this
repository's Vaultwarden runtime model and should be preferred over plain environment values.
Extensions and UI-created customizations enlarge the upgrade surface, so update acceptance must
include them when the lab uses them.
