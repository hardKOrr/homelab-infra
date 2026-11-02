# Servarr (Prowlarr, Sonarr, Radarr, Lidarr)

## Native recovery

Prowlarr, Sonarr, Radarr and Lidarr share Servarr native recovery. Backup stops only the
selected instance and captures its config directory (including `<app>.db`, `config.xml`
and the verified API key) as one PBS `data.pxar` in `host/<instance>`. Logs, `logs.db`
and local `Backups` are excluded; media mounts remain outside recovery. Pruning retains
the two newest snapshots in addition to daily retention, so a same-day pre-restore safety
backup keeps the immediately preceding recovery point. Restore previews
without mutation until `overwrite=true`, validates the selected backup group and staged
database/configuration, then adopts the archived API key in Compose and the canonical
`homelab-infra/media/<instance>` item after an authenticated API check. A subsequent
Deploy keeps that key and the recovered application state.
Restore requires both Compose API-key overrides before replacing state, and retains the
original config directory beside the live path until verified recovery succeeds. A failed
restore puts the original directory and Compose API-key overrides back and starts it.
