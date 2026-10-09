# homelab-infra

## What this is

Ansible deploys and operates applications on Proxmox; Rundeck provides the operator UI.
Deploy jobs provision guests and wire routes, SSO, monitoring, and DNS for the selected providers.
The platform manages only guests tagged `_+lab` and the resources its playbooks create.
Ansible remains independent of Rundeck.

## Prerequisites

- Proxmox VE 8 or 9 cluster, root access on a bootstrap node, and a free runner IP.
  The platform derives the runner VMID from its address; no VMID input is needed.
- A domain and DNS zone you control. The default Caddy DNS-01 setup needs a Cloudflare
  API token scoped to Zone Read and DNS Edit.
- Caddy must reach a resolver over UDP/TCP 53 that returns the public zone's SOA.
  DNS-01 uses the selected network's `dns_servers` (`LAB_NET_DNS` during bootstrap).
  `CT_DNS` changes only the runner resolver. SOA discovery still runs when TXT propagation
  checking is disabled; split DNS must not substitute a different zone.
  See the [resolver example](config.example/infrastructure.yml) for overrides.

Enrollment uses `https://vaultwarden.<lab-domain>` on the new Caddy LXC. Before enrollment:

- Create a LAN resolver record for that name pointing at Caddy, reachable from the runner
  and your workstation. Later deploys create records through the configured DNS provider;
  this first record is required before cutover makes its API credential available.
- Permit client and runner traffic to Caddy on ports 80/443 across VLANs or subnets.
- Set `reverse_proxy.internal_cidrs` to the client source networks. Caddy restricts
  `routing.access: internal` routes (the default) to those networks. Split DNS is not access
  control; select `routing.access: public` explicitly for unrestricted routes.

DNS-01 proves domain control through the provider API. No public A record or inbound WAN
port is required. An existing WAN reverse proxy keeps its ports; lab Caddy uses its own IP.

## Bootstrap

Copy the script to a Proxmox node and run it as root:

```sh
scp rundeck/bootstrap-rundeck.sh root@<pve-node>:/root/
ssh root@<pve-node> 'bash /root/bootstrap-rundeck.sh'
```

It prompts for domain, owner and automation emails, network, timezone, and providers.
For unattended input, load a root-owned `0600` environment file containing the token,
without shell tracing, then run on the node:

```sh
set +x
set -a; . /root/bootstrap-private.env; set +a  # defines CLOUDFLARE_API_TOKEN
LAB_DOMAIN='<lab-domain>' NONINTERACTIVE=1 bash /root/bootstrap-rundeck.sh
```

The script builds:

- A Debian 13 Rundeck LXC with the pinned Ansible toolchain, checkout, project, and jobs.
- A scoped Proxmox API user/token, guest SSH identity, encrypted Key Storage, and config.
- Caddy and HTTPS Vaultwarden in temporary Seed mode, then the enrolled Vault platform.
- Ntfy, Authentik, Uptime Kuma, Prometheus + Grafana, and PBS through **Bootstrap Platform**.

Re-running converges without rotating credentials or overwriting saved answers. If the
Vaultwarden name does not yet resolve to Caddy, create the record and rerun. The script
resumes enrollment, cutover, or Bootstrap Platform according to the saved markers.
These three jobs can also be run in that order in Rundeck without input.
Set `DEPLOY_VAULTWARDEN=0` only for deliberate runner-only recovery.

The script generates both Vaultwarden master passwords, records them beside the Rundeck
admin password in root-only `/root/.rundeck-bootstrap`, and stages a copy for the job user.
**Vaultwarden Enrollment** invites the declared addresses with public signups off,
registers both accounts, creates the `homelab-infra` organization, confirms automation as
an **Admin**, and creates missing encrypted Key Storage entries:

- `keys/project/homelab-infra/vaultwarden-machine/client-id`
- `keys/project/homelab-infra/vaultwarden-machine/client-secret`
- `keys/project/homelab-infra/vaultwarden-machine/master-password`

Enrollment leaves existing entries alone. Rundeck cannot return a stored password to detect
an incorrect entry; delete that entry in Key Storage and rerun Enrollment.
**Vaultwarden Cutover** imports every seed secret, including both master passwords, reads
each back, writes `state/vault-mode`, deletes the job user's seed files, and writes
`state/cutover-complete`. If cleanup fails between markers, the next script run removes
leftover seed files as root before Bootstrap Platform.

To sign in as owner, read `VAULTWARDEN_OWNER_PASSWORD` from `/root/.rundeck-bootstrap`.
Change it in the web vault, then edit `owner_master_password` in the
`homelab-infra/vaultwarden` item while still signed in. Delete the handover line afterward;
the password never passes through a job. The script removes automation's root password
copy after cutover. Bootstrap Platform reconciles tagged Caddy/Vaultwarden and deploys
the remaining baseline services. Fix a failed step and rerun the script or job.

To run `lab-run` without Rundeck, run **Setup / Credentials / Vaultwarden Login File** once
with an Ansible Vault password you keep. It stores the automation login in
`config/vaultwarden.yml`, encrypted with that password; see
[Running directly](ansible/README.md#running-directly).

## Deploying an app

Run the application's **Deploy** job in Rundeck. Configure overrides through its
**Configure** job or `config/apps/<instance>.yml`, then redeploy.

## Configuration

`config/` is gitignored runtime state on the runner and survives checkout refreshes.
Platform defaults merge recursively with `config/proxmox.yml`, then `config/infrastructure.yml`.
Application defaults merge recursively with `config/apps/<instance>.yml` for each instance.
Generated topology and Vaultwarden runtime secrets complete the in-memory service registry.
See the [variable contract](ansible/vars/CONTRACT.md) and [examples](config.example/README.md).

## Secrets

| Secret | Home |
|---|---|
| Vault automation client ID, client secret, master password | AES-GCM-encrypted Rundeck Key Storage; the master password also in `homelab-infra/vaultwarden` (`automation_master_password`); once **Vaultwarden Login File** has run, also `config/vaultwarden.yml`, encrypted with your Ansible Vault password |
| Vaultwarden owner master password (generated) | root-only `/root/.rundeck-bootstrap` on the runner until you change it and delete the line; also `homelab-infra/vaultwarden` (`owner_master_password`), which you edit in the web vault when you change it |
| Vaultwarden admin token | AES-GCM-encrypted external runner storage; it administers the server but cannot decrypt vault items |
| Cloudflare DNS-01 token | temporary AES-GCM runner storage during Seed mode, then `homelab-infra/reverse_proxy` in Vaultwarden |
| Proxmox, runner SSH, and generated service credentials | canonical organization-owned Vaultwarden items after verified cutover |
| Rundeck API token | AES-GCM Key Storage, injected only into control-plane jobs |
| Anything authored after cutover (a second domain's DNS-01 token, a firewall API key) | typed into the **Store Secret** job, which writes it straight into its canonical Vaultwarden item — it is never written to disk |

Ansible Vault encrypts only `config/vaultwarden.yml`, and its password stays with the
operator. Seed files are temporary bootstrap inputs. After cutover,
mutating jobs unlock Vaultwarden before Ansible starts and fail closed if unlock fails.
`config/.generated/facts.yml` holds topology only and rejects secret-shaped fields.

## Recovery

Keep independent PBS/PVE access, backup artifacts, the Rundeck converter password,
Vaultwarden unlock material, and your Ansible Vault password outside the components they
recover. Restore the runner
stopped or isolated, recover both encrypted storage namespaces, then recover the vault
before ordinary jobs. Follow [runner recovery](rundeck/RUNNER-RECOVERY.md) and
[Vaultwarden recovery](rundeck/VAULTWARDEN-RECOVERY.md) for prerequisites and ordering.

## You want to…

| You want to… | Use |
|---|---|
| Run or reimport jobs | [Rundeck](rundeck/README.md) |
| Run or change Ansible | [Ansible](ansible/README.md) |
| Configure an app | Its **Configure** job or [examples](config.example/README.md) |
| Look up a config key | [Variable contract](ansible/vars/CONTRACT.md) |
| Add an app | [App authoring](ansible/playbooks/apps/README.md) |
| Add an estate | [Estate onboarding](docs/estate-onboarding.md) |

## GitHub coordination

For AO work, GitHub issues own scope and dependencies; PR comments preserve decisions,
meaningful milestones, blockers, review fixes and final validation. Keep the PR body
current too. Before a PR exists, use the issue. Preserve any issue-specific requirement
for a single final result comment. Public text uses [lab placeholders](docs/lab-placeholders.md).

Select authorized work with `ao:ready` and one explicit dependency field in the issue body:
`<!-- ao-dependencies: none -->` or `<!-- ao-dependencies: #123 #456 -->`. Dependencies
must be closed; `ao:blocked` prevents admission. The watcher admits one issue at a time
through assignment, checking task ownership and usage limits. Orchestrators plan or
handle operator-requested exceptions; they do not spawn around the queue.

Workers append an event marker to each significant comment, substituting their actual
`AO_SESSION_ID`: `<!-- ao-event session=homelab-infra-123 kind=checkpoint -->`.
Use `kind=blocked` for one concrete question or unmet prerequisite, then stop; the watcher
alerts the operator once with the comment link. Use `kind=done` only after the full issue
scope and required validation pass. Failed, unverified and partial results never count
as done. Include the tested SHA, commands/results and remaining limits in the final comment.

Answer with a new issue/PR comment whose first line is
`/ao continue homelab-infra-123`, optionally followed by the decision. Only an explicit
command from the configured operator resumes that worker; ordinary comments do not.
After resuming, publish fresh completion before merge or retirement. Never include secrets.

AO delivers CI/review feedback directly to the owning worker. Do not use `ao report`,
send routine coordination messages, relay that feedback again, acknowledge receipts or
poll other agents for status. Claim the PR and make it ready once acceptance and checks
pass; use `Closes #<issue>` only when its full scope is satisfied. The watcher verifies
current-head approval, CI and mergeability, then merges, closes the completed issue and
retires the idle worker. Existing ownership, recovery and serial live-operation guards apply.
