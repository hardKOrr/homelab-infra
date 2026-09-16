# Notes

## 2026-09-15 — repository implementation

Navidrome's Docker contract uses `deluan/navidrome`, a Compose `user:` mapping, writable
`/data`, and a read-only `/music` bind. The implementation derives the source from the
existing `media_storage.library` plus the app's relative `library_subpath`; it does not
bind the whole media mount and never creates or deletes the music path. Navidrome's first
administrator remains an app-owned web setup step because its documented CLI requires an
existing admin before it can manage users.

No provider account, live stack host, or real music mount was inspected or changed. The
proposed disposable instance and stack names are recorded above, while #198 must authorize
and name the exact fixture paths before live acceptance.
