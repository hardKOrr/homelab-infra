# App-level layering

The per-app merge (`vars/app-defaults/<app>.yml` → `config/apps/<instance>.yml` → `app_config`)
runs separately in each play. It is independent of the `homelabinfra_config` merge in [§4](../CONTRACT.md#4-merge-order-low--high-precedence).

**Native LXC template precedence.** Lowest to highest: the repository fallback at
`homelabinfra_defaults.proxmox.lxc.ostemplate`, the authored lab value at
`config/proxmox.yml` → `proxmox.lxc.ostemplate`, then an explicit instance value at
`config/apps/<instance>.yml` → `proxmox.ostemplate`. App defaults deliberately omit
`ostemplate`: the recursive provisioning overlay preserves the platform value when
the instance has not set it. This applies to Caddy, Vaultwarden, Ntfy and the other
native LXC consumers; app resource sizing still overlays the lab's LXC defaults.
The repository fallback is `local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst`;
it is not a download instruction or a claim that this image exists on every node.
The selected volume must already exist on a template store available to the target
node. An authored value may name a shared store and a different Debian release.
Template resolution does not migrate or recreate an existing instance guest.

**Instance-file schema.** `config/apps/<instance>.yml` is loaded whole by
filename. The filename is the instance name (`-e instance=<name>`) and identifies the
guest, application record, and provider objects. Routing uses `routing.subdomain` as
described below. VM instances may set `proxmox.vm.balloon` to a positive whole-number
minimum in MiB, no greater than `proxmox.vm.memory` (the maximum). Leave it unset to
preserve Proxmox's default on creation and the existing guest's configured value on
convergence. VM instances may set `proxmox.vm.ha: true` to create and maintain a Proxmox
HA resource in state `started`; it defaults off. Disabling it removes only the resource
whose comment is the exact homelab-infra ownership marker. HA is refused when any VM disk
uses a Proxmox storage that is not explicitly marked `shared`, and the refusal names the
disk slot. Remove withdraws the owned HA resource before destroying its VM. For the k3s
cluster, `proxmox.vm.ha` applies to each node VM described by that cluster instance file.
Top-level keys mirror the `<app>_defaults` dict in
`vars/app-defaults/<app>.yml`: `proxmox:` (native LXC) **or** `stack:` (Docker apps — a scalar such
as `media`), `app:` (port, data_path, config_path, plus app-specific keys), optional `update:`
(`github_repo`, `binary_path` — native GitHub-release apps only), and `routing:` (`proxy`,
`access`, `identity`, plus optional `subdomain` and `estate`). `routing.proxy`
(`internal | external | none`) selects **which** reverse proxy serves the app in a two-proxy
topology; `none` means the app is not routed at all, which is how the reverse proxy itself
avoids routing itself. `routing.access` (`internal | public | authenticated`, default `internal`)
is a separate axis: it decides **who** may reach the app through that proxy. `internal` makes
`tasks/wiring/caddy.yml` add a `remote_ip` matcher restricting the route to
`reverse_proxy.internal_cidrs`; `public` emits the route with no source matcher, which on a
WAN-facing Caddy publishes the app to the internet. `authenticated` emits the same open route as
`public` and additionally **asserts that
the app is really gated**: `routing.identity` must be `forward_auth` or `oidc` and `sso.provider`
must not be `none`. An `authenticated` route whose identity mode is `catalog` (a launch tile) or
`none` (no object at all) is an app the operator believes is protected and which is in fact open
to the internet. Access and identity stay separate fields — access is the network path, identity
is where authentication happens — and this is the one point at which the two must agree.
Enforcement of all three classes is Caddy's: `tasks/wiring/nginx.yml` does not read the access
class at all, so an Nginx lab publishes every route with no source matcher.
`routing.proxy: external` does not widen access — exposure is only ever
`routing.access`. `routing.identity` is the identity-mode
enum `none | catalog | oidc | forward_auth` (default `catalog`): `none` skips Authentik
entirely, `catalog` creates an Application tile only, `oidc` creates an OAuth2 provider +
Application (client_id/secret handed back to the deploy as `authentik_oidc_client_id/_secret`
facts — not recorded in the registry), `forward_auth` creates a proxy provider enforced at the
reverse proxy. Enforcement is the reverse-proxy wiring's job: `tasks/wiring/caddy.yml` and
`tasks/wiring/nginx.yml` both read `wiring_identity_mode` alongside `sso.provider` +
`sso.host`, and emit the outpost handler chain (Caddy) or the Authentik `auth_request`
snippet (NPM) only for `forward_auth`; every other mode keeps the plain route it always
had. The boolean `routing.auth` is **superseded** by `routing.identity`
— nothing reads it. `routing.subdomain` is the hostname on the estate domain. **Every routed application
declares it in `vars/app-defaults/<app>.yml`**, and an instance file overrides it only to
publish that instance somewhere else. The fall-back when nothing declares it is the
instance name, and that fall-back is a trap rather than a feature: it couples the published
URL to the filename, so an estate-scoped instance would publish
`<app>-<estate>.<domain>`. Declaring the subdomain per application is what leaves the
instance name free to carry identity — filename, guest hostname, Proxmox tag — without the
URL following it around. `routing.estate` names a `domains:` estate ([estates](estates.md)) and is authored per instance.
`app-defaults/` must not declare one and `rundeck/render-job.py` rejects it: that file is
git-managed and ships to every lab, so an estate named there is one the next clone has
never declared, and its config-doctor refuses the instance that inherits it.
For a Docker app whose defaults declare a `stack:`, a non-empty `app.gpu_device` selects
the stack that owns that device: `proxmox.devices.<device>.stack`, or the device key when
`stack` is omitted. This applies during Deploy and application recovery/removal. The app's
default stack is retained when no GPU is declared. An instance file may omit its top-level
`stack` to inherit the app default and let the GPU select another stack; if it explicitly
sets `stack`, config-doctor requires that value to match the device's effective stack.
Apps deployed as dedicated GPU VMs without a `stack:` use their existing VM placement path.

Media-stack instances may add three optional `app:` keys read only by
`wire-media-stack.yml`: `media_kind` (the app kind — its presence is what enrols
the instance in media wiring), `host` (an explicit base URL, for an app this lab
did not deploy) and `api_key` (when the app's key cannot be read from
`config_path/config.xml`).
The whole file merges over `<app>_defaults` via `combine(recursive=True)`, later layer wins per key.
Because the merge is recursive, an override must match the default's shape: replacing a mapping (e.g.
`app:`) with a scalar clobbers the entire subtree, so instance files never restate a mapping key as a
bare scalar.

When `infrastructure.yml` declares two or more estates, an estate-scoped catalog application
must use `<app>-<estate>[-<variant>]` as its instance name and must author the same estate in
`routing.estate`. There is no unsuffixed default estate. Lab-scoped platform services keep
their ordinary instance names because one deployment serves the whole lab.
