# Bazarr

## Native recovery

Bazarr native recovery stops only the selected service and archives its config directory
as one PBS `data.pxar` in `host/<instance>`, excluding `log` and `cache`. Backup retains
the two newest snapshots alongside daily retention, preserving the selected recovery
point when the fresh instance is backed up. Restore previews until `overwrite=true`,
checks the source group, and retains the original directory on the same filesystem until
an authenticated settings check and canonical API-key publication succeed. A failure
puts the original state back and starts it. The final Deploy keeps the
restored API key, language profiles and settings.
