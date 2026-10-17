# Frigate deployment and recovery

Frigate runs in the dedicated `stack-frigate` Docker-on-VM guest. Select
`app.detector: coral` for the existing accelerator setup or `app.detector: cpu` for a
device-free instance. CPU mode requires empty `gpu_device`, `igpu_render_node`,
`coral_usb_mapping` and `hwaccel_args` values; the playbook does not inspect or bind device
paths in that mode. Camera endpoints and MQTT remain optional.

The web host port is `app.port`; RTSP and WebRTC host ports derive from it (+1 and +2)
unless `rtsp_port` and `webrtc_port` are explicitly set. Give a second instance a distinct
`app.port` because the Frigate stack host is shared by instances. Instances with cameras
must declare an existing Proxmox-node recording mount; the role never adopts, formats or
deletes it. Camera-less instances can leave `recordings_path` empty and need no recording
storage. Any configured recording mount remains outside native application recovery and
needs its storage owner's own snapshot or backup.

Ansible seeds `/opt/<instance>/config/config.yml` on first deploy. After that, Frigate's UI
owns configuration edits; Deploy preserves the file so a normal convergence does not
erase saved settings. The whole config directory includes `frigate.db` and is the native
recovery unit. Backup stops only the Frigate service, archives that directory to PBS as
`host/<instance>/data.pxar`, and starts it again. Recordings stay outside the archive.
The seed disables Frigate's own TLS because Caddy terminates TLS before proxying to port
8971; Frigate's reverse-proxy guide requires this for the internal HTTP connection.
Deploy and Restore check the public `/api/auth/first_time_login` endpoint because port
8971 requires authentication for `/api/version` before the operator can log in. See
Frigate's [first-time login API](https://docs.frigate.video/integrations/api/first-time-login-auth-first-time-login-get/).
Restore previews by default, validates the selected snapshot group and staged config and
database, then keeps the original directory beside the live path on the same filesystem
until the restored service passes health checks. Any later failure puts the original
directory back and leaves Frigate stopped.

## Recovery procedure

1. Restore or create the `stack-frigate` guest and restore its declared recording-storage
   snapshot separately when recordings must be recovered.
2. Deploy the instance, then run Restore App with `overwrite=false` to select a Frigate
   recovery point. For overwrite, first back up the fresh target and pass that point as
   `pre_restore_point`.
3. Verify login and the recovered config and database, then run Deploy to confirm
   convergence. In Coral mode, removal detaches only mappings with this platform's
   provenance tag; a disconnected Coral remains an explicit reconnect-and-retry condition.

Camera credentials and the optional MQTT password are read from hidden fields in
`homelab-infra/apps/<instance>` in Vaultwarden. They are rendered only into the root-owned
initial `config.yml`; secret-bearing Ansible tasks use `no_log: true`.
