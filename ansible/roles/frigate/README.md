# Frigate deployment and recovery

Frigate runs in the dedicated `stack-frigate` Docker-on-VM guest, or on Docker on an LXC (CPU-only) when configured with `stack: frigate-lxc`. Select
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

Ansible seeds `/opt/<instance>/config/config.yml` only when the file is absent
(`force: false`). After first deploy, changing `app.cameras`, `app.detector`, `app.mqtt`,
or the camera and MQTT credentials in Vaultwarden's homelab-infra fields does not update
the existing Frigate config, even though the role continues to validate those inputs.
Make those changes in Frigate's UI or API, or deliberately remove the instance's
`config.yml` before Deploy to seed it again from homelab-infra. Reseeding replaces
UI-managed configuration. This lets Deploy preserve saved settings during normal
convergence. The whole config directory
includes `frigate.db` and is the native recovery unit. Backup stops only the Frigate
service, archives that directory to PBS as
`host/<instance>/data.pxar`, and starts it again. Recordings stay outside the archive.
The seed disables Frigate's own TLS because Caddy terminates TLS before proxying to port
8971; Frigate's reverse-proxy guide requires this for the internal HTTP connection.
Deploy and Restore check the public `/api/auth/first_time_login` endpoint because port
8971 requires authentication for `/api/version` before the operator can log in. See
Frigate's [first-time login API](https://docs.frigate.video/integrations/api/first-time-login-auth-first-time-login-get/).
Restore previews by default, validates the selected snapshot group and staged config and
database, then keeps the original directory beside the live path on the same filesystem
until the restored service passes health checks. Any later failure puts the original
directory back and starts it.

## Native

Choose `hosting.method: native` and `proxmox.guest_type: lxc` or `vm`; Frigate then runs on a
guest of its own, CPU-only: Deploy refuses a Coral, GPU, camera, MQTT user or
`recordings_path` for this method. `app.native_version` pins the upstream source release
(the image tag carries the same version) and `app.native_sha256` its archive; the other
prebuilt pieces (Python 3.11 for upstream's TFLite wheel, Node.js for the web build,
go2rtc, tempio, yq, ffmpeg, the CPU model) are pinned with their checksums in
`app.native_assets`. The private Python runtime leaves the guest's system Python intact.
`tasks/native-install.yml` builds nginx with Frigate's modules, sqlite-vec, pysqlite3, the
Python environment and the web UI with upstream's own scripts, so the first Deploy takes a
while; later Deploys skip every finished step. Upstream's s6 services run as three systemd
units (`<instance>`, `<instance>-nginx`, `<instance>-go2rtc`). `/config` links to
`app.config_path`; nginx serves `app.port` and keeps the unauthenticated internal API on
loopback. Recovery archives the same config directory as Docker, through the shared
backup and restore operations.

## Kubernetes

With `hosting.method: kubernetes` Frigate runs CPU-only in the app's namespace: no camera
credentials, Coral, GPU or MQTT user (Deploy refuses them). `config_storage` and
`media_storage` size two default-StorageClass claims; only the config claim (`config.yml`
and `frigate.db`) is in the `data` recovery archive, backed up by the shared CronJob and
restored by the shared Job. `config.yml` is seeded once and then belongs to Frigate.

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
