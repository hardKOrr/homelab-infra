# homelab-infra

One click deploys a fully configured, cross-wired application onto Proxmox.

Deploying an app creates its guest, installs it, publishes it through your reverse proxy,
registers it with your SSO, adds an uptime monitor and creates its DNS record — in one
job, with no follow-up steps. Removing it unwires all four again.

You clone this repo, run one command on a Proxmox node, and click one button. That is the
whole path.

---

## The two bootstrap layers

Everything in this project happens in one of two places, and knowing which is which makes
the rest of the documentation obvious.

| | Layer 1 — the runner | Layer 2 — the lab |
|---|---|---|
| **What** | `rundeck/bootstrap-rundeck.sh` | the **Bootstrap Platform** job |
| **Where** | as root on a Proxmox node | in the Rundeck/Semaphore UI |
| **Builds** | the runner, Caddy, and Vaultwarden in temporary Seed mode | the remaining services in mandatory Vault mode |
| **Produces** | Rundeck/Ansible, config, jobs, encrypted Key Storage, HTTPS Vaultwarden, and explicit enrollment/cutover jobs | reconciled Caddy/Vaultwarden, Ntfy, Authentik, Uptime Kuma, Prometheus + Grafana, PBS |
| **Run it** | once, by hand | once, by clicking |

Between them is one unavoidable human ceremony: choose the owner and automation-account
master passwords inside Vaultwarden, create the automation API key, stage its three values
as encrypted job secrets, and run the verified cutover. The initial script brings up every
component needed to perform that ceremony; it never asks for those passwords.

### 1. Stand up the runner

On any Proxmox node, as root:

```sh
scp rundeck/bootstrap-rundeck.sh root@<node>:/root/
ssh root@<node> 'bash /root/bootstrap-rundeck.sh'
```

It asks for the lab domain, first-owner and automation-account email addresses, the guest network,
a timezone, and which reverse proxy / SSO / notification / DNS providers you want. Every
answer has a default except the domain, and every answer can be supplied as an environment
variable instead, so the whole thing scripts:

```sh
LAB_DOMAIN=lab.example.com NONINTERACTIVE=1 bash bootstrap-rundeck.sh
```

Everything else it works out for itself: the node name, the API address, storages,
bridges, template storage, the timezone. It then creates a dedicated `homelab-infra@pve`
Proxmox user with a scoped role, mints that user's API token and puts it straight into
Rundeck Key Storage, generates the SSH key the platform will use to reach its guests,
writes `config/proxmox.yml` and `config/infrastructure.yml`, creates the Rundeck project,
imports every job, and tags its own container so the platform manages it like any other
guest it created. Its last deployment sequence brings up Caddy first, then Vaultwarden and
its HTTPS route, and sends the exact owner invitations while keeping public signups off.
Set `DEPLOY_VAULTWARDEN=0` only for deliberate runner-only recovery.

Re-running it converges: it rotates no credential and overwrites no answer you already
gave.

### 2. Stand up the lab

Open the Rundeck URL it printed. Complete **Vaultwarden Enrollment**, create the dedicated
automation account, `homelab-infra` organization and `platform-secrets` collection, then
stage the account's client ID, client secret and master password in the named encrypted
Key Storage entries. Run **Vaultwarden Cutover**; it imports and reads back every seed
secret before writing the marker and deleting seed files. Then run **Bootstrap Platform**.

That reconciles the already-tagged Caddy and Vaultwarden LXCs, then deploys Ntfy, Authentik, Uptime Kuma,
Prometheus + Grafana and PBS. Each step records its own connection details before the next
one needs them, so the run is resumable: if something fails, fix it and run the job again.

### 3. Deploy things

One job per app, no parameters to fill in. Click **Deploy Sonarr** and you get a Sonarr,
routed at `sonarr.yourdomain.com`, showing up in Authentik, monitored by Uptime Kuma and
resolvable in DNS.

---

## Where to go next

| You want to… | Read |
|---|---|
| Understand the jobs and how to import them | [`rundeck/README.md`](rundeck/README.md) |
| Use Semaphore instead of Rundeck | [`semaphore/README.md`](semaphore/README.md) |
| Change an app's configuration | the **Configure App** job, or [`config.example/`](config.example/) |
| Know exactly what a config key does | [`ansible/vars/CONTRACT.md`](ansible/vars/CONTRACT.md) |
| Add a new app | [`ansible/playbooks/apps/README.md`](ansible/playbooks/apps/README.md) |
| See what is built and what is planned | [`.claude/meta/INDEX.md`](.claude/meta/INDEX.md) |

---

## How configuration works

Three layers, merged per key. You write only what differs from the defaults.

```
ansible/vars/homelabinfra-defaults.yml   global defaults        (in git)
ansible/vars/app-defaults/<app>.yml      per-app defaults       (in git)
config/apps/<instance>.yml               your overrides         (gitignored, on the runner)
```

`config/` is **gitignored and lives on the runner**. That is deliberate and it is what
makes the runner's self-refresh safe: before every job, the checkout resets hard to the
tracked branch, and because nothing under `config/` is tracked, your configuration
survives untouched. A fix pushed to the repo therefore reaches your platform on the next
click, with no action from you, and the job log names the commit it ran.

You never need SSH to read or change that configuration. Three jobs in the **Config**
group do it from the UI:

- **Configure App** — writes `config/apps/<instance>.yml` from a form. Every field is an
  override; blank fields keep their current value. The previous content is kept under
  `.backups/` and the job log shows a diff of exactly what changed.
- **Get Config** — reads the whole set back out, secrets redacted, plus an unredacted
  archive on the runner as a restore point.
- **Config Doctor** — validates everything and names every problem by file and key path.
  It also runs in front of every other job, so a missing key fails at the front door
  instead of halfway through provisioning something.

## Where secrets live

| Secret | Home |
|---|---|
| Vault automation client ID, client secret, master password | AES-GCM-encrypted Rundeck Key Storage, or Semaphore encrypted secret variables |
| Vaultwarden admin token | AES-GCM-encrypted external runner storage; it administers the server but cannot decrypt vault items |
| Cloudflare DNS-01 token | temporary AES-GCM runner storage during Seed mode, then `homelab-infra/reverse_proxy` in Vaultwarden |
| Proxmox, runner SSH, and generated service credentials | canonical organization-owned Vaultwarden items after verified cutover |
| Rundeck API token | AES-GCM Key Storage, injected only into control-plane jobs |

There is **no Ansible Vault**, ever. Seed files exist only while bringing up Caddy and
Vaultwarden. After the explicit cutover marker, every mutating job unlocks Vaultwarden
before Ansible starts and fails closed if it cannot. `config/.generated/facts.yml` contains
topology only; secret-shaped fields are rejected.

## What this project will and will not do

- **It manages what it creates.** Guests it did not create are never touched — that is
  enforced by a `homelab-infra` tag, not by convention. Point it at a lab full of
  hand-built machines and it will ignore every one of them and build its own beside them.
- **It provisions; it does not police.** Deploying creates the thing correctly. It does
  not run forever reconciling drift.
- **It configures tools rather than replacing them.** Watchtower updates containers,
  unattended-upgrades updates the OS, PBS handles backups, Uptime Kuma watches uptime,
  Ntfy delivers every notification. This project sets them up and gets out of the way.
- **Defaults cover the ordinary case.** You configure what differs, not what is normal.

## Requirements

- Proxmox VE 8 or 9, with root on a node
- One free IP and VMID for the runner
- A domain you control (it does not need to be public; internal-only labs work)
- A Cloudflare API token scoped to Zone Read plus DNS Edit for that domain when using the default Caddy DNS-01 setup; no public app records or inbound WAN ports are required

Debian 13 for the runner is not incidental: `community.proxmox` needs ansible-core ≥ 2.17,
which needs a Python 3.11+ controller.
