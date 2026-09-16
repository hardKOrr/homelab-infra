# Notes

## 2026-09-15 — repository implementation

Emby remains a distinct product from Jellyfin and Plex, not a Servarr registry kind. Its
host port defaults to 8097 so it can coexist with Jellyfin on 8096 while its container port
remains 8096. Its Compose mounts use the established media-storage source paths with Docker read-only mode;
only the Emby config directory is included in the role-owned recovery unit. Emby’s first
user is renamed by the Startup wizard and receives its password through the app’s
user-password API. No live target or real media mount was inspected or changed. The proposed
disposable instance/stack names are recorded above, while #35 must authorize and name the
exact fixture paths before any live acceptance is run.
