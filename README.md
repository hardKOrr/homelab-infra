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
| **Where** | as root on a Proxmox node | in Rundeck |
| **Builds** | the runner, Caddy, and Vaultwarden in temporary Seed mode | the remaining services in mandatory Vault mode |
| **Produces** | Rundeck/Ansible, config, jobs, encrypted Key Storage, HTTPS Vaultwarden, and explicit enrollment/cutover jobs | reconciled Caddy/Vaultwarden, Ntfy, Authentik, Uptime Kuma, Prometheus + Grafana, PBS |
| **Run it** | once, by hand | once, by clicking |

Between them is one unavoidable human ceremony: choose the owner and automation-account
master passwords inside Vaultwarden, create the automation API key, stage its three values
as encrypted job secrets, and run the verified cutover. The initial script brings up every
component needed to perform that ceremony; it never asks for those passwords.

### 0. Make the lab domain reach the lab Caddy

One network prerequisite has to be true before the ceremony in the middle is possible, and
it is the only thing this project cannot arrange for you.

Layer 1 finishes by putting Vaultwarden behind an HTTPS route on the Caddy it just built,
and the enrollment ceremony is performed in a browser at `https://vaultwarden.<your
domain>`. That URL has to resolve — from the runner and from your workstation — to the new
Caddy LXC, and the path to it on ports 80/443 has to be open. So:

- **Resolution.** Create the record for `vaultwarden.<your domain>` pointing at the Caddy
  LXC in whatever resolver your LAN uses. Once `dns.provider` is configured and Vaultwarden
  holds its API key, later app deploys create their own records automatically — this first
  one is the exception, because it is what the cutover that unlocks that key depends on.
- **Reachability.** If clients and the Caddy LXC sit on different VLANs or subnets, the
  router has to permit that traffic to 80/443. Same-subnet labs have nothing to do.
- **Source networks.** Caddy enforces `reverse_proxy.internal_cidrs` on every app whose
  `routing.access` is `internal` (the default), so list the subnets your clients actually
  come from. Split DNS is not treated as an access control. An app is reachable from
  anywhere only when you set `routing.access: public` on it deliberately.

None of this involves the public internet. Certificates are obtained over **DNS-01**
(`reverse_proxy.dns_challenge`), which proves domain control through your DNS provider's
API — the CA never connects to your lab, so no public A record and no inbound WAN port is
required. If you already run a reverse proxy on your WAN's 80/443, it keeps those ports and
is untouched: the lab Caddy listens on its own address.

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

Open the Rundeck URL it printed. Two jobs, with one browser session between them.

Layer 1 already sent the owner and automation invitations itself. The **Vaultwarden
Enrollment** job re-sends them, and you click it only if that attempt failed — which
happens when `vaultwarden.<domain>` did not yet resolve to the Caddy LXC.

In the web vault at `https://vaultwarden.<domain>`, register the owner address and the
automation address. **You choose both master passwords here** — nothing in this project
generates, stores or prints them, which is why the job output has no password in it. Then,
as the owner, create the `homelab-infra` organization and invite the automation account
into it as an **Admin**.

That is the whole manual step. Do not create any collection or assign collection
permissions: the platform creates `platform-secrets` on first write. The web vault's
auto-created "Default Collection" is ignored. Admin membership gives the automation
account organization-wide access, so Vaultwarden stores and displays no explicit
permission for that account on `platform-secrets`.

Signed in as the automation account, view its personal API key (Settings → Security → Keys).
Stage that client ID and client secret, plus the automation master password you chose, in
these encrypted Password entries:

- `keys/project/homelab-infra/vaultwarden-machine/client-id`
- `keys/project/homelab-infra/vaultwarden-machine/client-secret`
- `keys/project/homelab-infra/vaultwarden-machine/master-password`

Run **Vaultwarden Cutover**; it imports and reads back every
seed secret before writing the marker and deleting seed files. Then run **Bootstrap
Platform**.

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
| Understand or change the Ansible implementation | [`ansible/README.md`](ansible/README.md) |
| Change an app's configuration | that app's **Configure** job, or [`config.example/README.md`](config.example/README.md) |
| Know exactly what a config key does | [`ansible/vars/CONTRACT.md`](ansible/vars/CONTRACT.md) |
| Add a new app | [`ansible/playbooks/apps/README.md`](ansible/playbooks/apps/README.md) |
| Inspect the legacy Semaphore reference | [`semaphore/README.md`](semaphore/README.md) |
| See what is built and what is planned | [`docs/meta/INDEX.md`](docs/meta/INDEX.md) |

---

## How configuration works

Configuration has two independent streams, and both merge recursively so you write only
what differs from the defaults:

- Platform configuration starts with `ansible/vars/homelabinfra-defaults.yml`, then applies
  `config/proxmox.yml` and `config/infrastructure.yml`.
- Application configuration starts with `ansible/vars/app-defaults/<app>.yml`, then applies
  `config/apps/<instance>.yml` for that instance.

The exact schemas and precedence rules live in
[`ansible/vars/CONTRACT.md`](ansible/vars/CONTRACT.md).

`config/` is **gitignored and lives on the runner**. That is deliberate and it is what
makes the runner's self-refresh safe: before every job, the checkout resets hard to the
tracked branch, and because nothing under `config/` is tracked, your configuration
survives untouched. A fix pushed to the repo therefore reaches your platform on the next
click, with no action from you, and the job log names the commit it ran.

You never need SSH to read or change that configuration. Four jobs do it from the UI —
one per application, and three under **Manage > Configuration**:

- **Configure &lt;App&gt;**, in that application's own Maintenance folder — writes
  `config/apps/<instance>.yml` from a form. Every field is an override; blank fields keep
  their current value. The previous content is kept under `.backups/` and the job log shows
  a diff of exactly what changed.
- **Get Config** — reads the whole set back out, secrets redacted, plus an unredacted
  archive on the runner as a restore point.
- **Store Secret** — puts a credential into Vaultwarden without a file ever existing on
  the runner. Cutover is a one-time import, so this is how anything authored later — a
  second domain's DNS-01 token, a firewall API key, a rotated password — gets in. One
  field per run; run it twice with the same item to store a key and its secret.
- **Config Doctor** — validates everything and names every problem by file and key path.
  It also runs in front of every other job, so a missing key fails at the front door
  instead of halfway through provisioning something.

## Where secrets live

| Secret | Home |
|---|---|
| Vault automation client ID, client secret, master password | AES-GCM-encrypted Rundeck Key Storage |
| Vaultwarden admin token | AES-GCM-encrypted external runner storage; it administers the server but cannot decrypt vault items |
| Cloudflare DNS-01 token | temporary AES-GCM runner storage during Seed mode, then `homelab-infra/reverse_proxy` in Vaultwarden |
| Proxmox, runner SSH, and generated service credentials | canonical organization-owned Vaultwarden items after verified cutover |
| Rundeck API token | AES-GCM Key Storage, injected only into control-plane jobs |
| Anything authored after cutover (a second domain's DNS-01 token, a firewall API key) | typed into the **Store Secret** job, which writes it straight into its canonical Vaultwarden item — it is never written to disk |

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
- **It uses the system that owns the concern.** Where an established tool already provides
  the needed behavior, the project configures and integrates it. Project-owned automation
  remains appropriate for orchestration and behavior no component owns.
- **Defaults cover the ordinary case.** You configure what differs, not what is normal.

## Requirements

- Proxmox VE 8 or 9, with root on a node
- One free IP and VMID for the runner
- A domain you control (it does not need to be public; internal-only labs work)
- A Cloudflare API token scoped to Zone Read plus DNS Edit for that domain when using the default Caddy DNS-01 setup; no public app records or inbound WAN ports are required
- A LAN resolver entry pointing the domain tree at the Caddy LXC, and router rules allowing your clients to reach it on 80/443 — see [step 0](#0-make-the-lab-domain-reach-the-lab-caddy)

Debian 13 for the runner is not incidental: `community.proxmox` needs ansible-core ≥ 2.17,
which needs a Python 3.11+ controller.
