# qBittorrent

## Native recovery

qBittorrent native recovery stops only the selected service and archives its config,
preferences and torrent resume metadata as one PBS `data.pxar` in `host/<instance>`.
Logs and downloads are excluded; payloads remain on the external media mount. Backup
keeps the two newest snapshots alongside daily retention. Restore previews until
`overwrite=true`, validates the backup group and staged configuration, and retains the
original config on the same filesystem until the canonical WebUI credential works.
Failure puts the original state back and starts it. Deploy preserves
recovered preferences and torrents and registers only this client in deployed torrent
consumers. Remove withdraws only entries that address the owned client.
