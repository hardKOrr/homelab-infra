# Deemix

## Native recovery

Deemix native recovery stops only the selected service and captures all of
`config_path` (settings and the optional ARL) as one PBS `data.pxar` in
`host/<instance>`. Downloads stay outside the recovery unit. The two newest
snapshots are retained alongside daily retention so a pre-restore safety backup
keeps the prior recovery point. Restore previews until `overwrite=true`, checks
the source group, and keeps the original config on its filesystem until the
replacement passes health checks. Any later failure puts the original back and
starts it. An empty ARL supports settings-only use; the human
route still uses its declared Authentik access path. Deploy preserves an existing
or restored `.arl` when the configured ARL is empty, and publishes that effective
value to the canonical media credential for Lidarr. A non-empty configured ARL
rotates the file and restarts Deemix.
