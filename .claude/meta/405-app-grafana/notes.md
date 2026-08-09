# 405 — notes

## 2026-07-25 — implementation

Implementation complete and gate-verified; slice stays in-progress until a live
deploy confirms the acceptance items.

### Decision: one app, not two (README option A)

Required by the README's acceptance item 6. **Option A**, as the README itself
recommended: a single `observability` role, playbook and instance config covering
Prometheus, Grafana and a node exporter.

Reasoning: Grafana's only datasource is Prometheus, nobody runs one without the
other, and splitting them would mean two Semaphore jobs to stand up one capability —
directly against "one click per app". The app is named `observability` rather than
`grafana` because the deliverable is the stack; bootstrap step 6's staged comment
(which said `apps/grafana.yml`) was updated to match.

### What's in

- `roles/observability/` — compose stack, generated Prometheus config, provisioned
  Grafana datasource and dashboard, generated admin password with continuity.
- `roles/observability/files/homelab-nodes.json` — a real default dashboard (hosts
  up/down, CPU, memory, root filesystem, load) templated on a `$instance` variable.
- `playbooks/apps/observability.yml` — PATH A Docker; records `metrics` before wiring.
- `vars/app-defaults/observability.yml`, `config.example/apps/observability.example.yml`.
- `playbooks/bootstrap.yml` step 6 activated.

### Decision: Prometheus is not published

Prometheus binds `127.0.0.1` on the stack host. It has no authentication of its own,
so publishing it would hand every metric in the lab to anything on the LAN. Only
Grafana is wired to the reverse proxy, and `app.port` is Grafana's port so the
existing wiring contract needs no special-casing. `metrics.prometheus_host` records
the loopback URL for humans and on-host debugging, not as something other services
can reach.

### Decision: node_exporter added to the baseline

`tasks/guest-bootstrap.yml` now installs and enables `prometheus-node-exporter` on
every guest. The README flagged this as "consider"; without it Prometheus has almost
nothing to scrape and acceptance item 3 ("dashboard shows data from at least one
host") is hollow. It exposes host metrics only, no control surface, and nothing
publishes those metrics beyond the lab network.

**This changes every guest, not just this app's host** — a scope note worth carrying:
existing guests pick it up the next time their app's deploy re-runs guest-bootstrap.

### Decision: scrape targets are a deploy-time snapshot

The Prometheus config is generated from `groups['tag_homelab-infra']` when the role
runs. It is not live service discovery. Re-running this deploy is what picks up newly
added guests, which is consistent with CLAUDE.md's fire-and-forget model and avoids
giving Prometheus Proxmox API credentials. The notification message reports the
target count so a surprising number is visible immediately.

### Contract addition

New Shape B role key `metrics: {provider, instance, host, prometheus_host,
admin_user, admin_password}` (CONTRACT.md §3), plus `metrics` in
`write-generated-facts.yml`'s documented key list.

This supersedes the README's `observability: {grafana_url, prometheus_url,
grafana_admin_password}` sketch, which is the service-keyed shape CONTRACT.md §3
marks superseded — same correction slices 300 and 302 already made to their READMEs.

### Other

- Grafana's provisioned datasource has a fixed `uid: prometheus`; the provisioned
  dashboard references the datasource by uid, so a Grafana-generated random uid would
  leave every panel unbound.
- Data directories are chowned to the container uids (Grafana 472, Prometheus 65534)
  because both write their own volumes.
- The role verifies the datasource actually provisioned via
  `/api/datasources/uid/prometheus` — a datasource that failed to load leaves every
  panel blank, which is easy to miss until someone looks at the dashboard.
- Prometheus config changes fire a `/-/reload` rather than a container recreate, so
  the TSDB is not dropped when only the target list changed.
- `routing.auth: false` — Grafana has its own login and user model. The example config
  documents flipping it to put Authentik in front as well.

### Verification

- ansible-lint: clean (production profile).
- syntax-check: `playbooks/apps/observability.yml` and `playbooks/bootstrap.yml`
  clean. Repo-wide, only the pre-existing slice-502 stub fails.
- NOT verified live.

### Live acceptance TODO

- Grafana UI loads; admin signs in with `metrics.admin_password`.
- Prometheus datasource pre-configured and the default dashboard shows real data.
- node_exporter running on all `homelab-infra` guests, and scraped (check
  `up{job="nodes"}` in Prometheus).
- Re-run idempotent — in particular that the Grafana password is not rotated.
- Confirm the container uids (472 / 65534) still match the upstream images.

---

## 2026-08-09 — the usability assertions ran live

Rundeck execution 43 (`Deploy Observability`, revision `f9347b3`) — succeeded,
`monitoring-stack ok=76 changed=0`, so the assertions landed on an already-converged
stack rather than on a fresh install.

Both checks added in `f9347b3` executed and passed:

- **Assert Prometheus is scraping the targets this deploy rendered** — the active
  target set from `/api/v1/targets` matched the list this run wrote. This is the probe
  for the 2026-08-08 single-file bind-mount failure, which `/-/ready` reported 200
  through.
- **Assert Grafana can query Prometheus** — `/api/datasources/uid/prometheus/health`
  answered, proving the query path and not only that provisioning wrote a file.

`changed=0` on the same run is the idempotence half: the Grafana admin password was
not rotated by a re-run.

Still unobserved: **admin sign-in from a browser**, and that the default dashboard
renders real data to a human. Those need a person at a browser, not a deploy.

---

## Superseded planning text (moved from README, 2026-08-08)

The original option A / option B framing and the pre-build approach, kept for provenance.
Option A shipped; the `facts.yml` shape below was superseded by slices 200 and 014.

## Problem

Bootstrap step 6 is "Prometheus + Grafana". No role or playbook exists. CLAUDE.md lists them together — open question whether they share one stack/playbook or split into two apps.

## Files

To create — option A (one combined app):
- `ansible/roles/observability/{tasks,handlers,defaults,meta,templates}/...`
- `ansible/playbooks/apps/observability.yml`
- `ansible/vars/app-defaults/observability.yml`
- `config.example/apps/observability.example.yml`

Option B (two apps): `roles/grafana/`, `roles/prometheus/`, two playbooks, two configs.

## Approach

Recommendation: **option A**, one combined "observability" stack. They're tightly coupled (Grafana datasource = Prometheus) and users don't realistically run one without the other. Splits add ceremony without value here.

Docker-on-LXC. Compose includes:
- Prometheus with a scrape config built from inventory (every `tag_homelab-infra` host with node_exporter)
- Grafana with provisioned datasource pointing at the Prometheus service + a default homelab dashboard
- Optionally node_exporter as part of the bootstrap baseline so every host is scrapeable (consider adding to `guest-bootstrap.yml`)

Grafana admin password generated and stored in Vaultwarden.

Wire Caddy + Authentik + Uptime Kuma + DNS for **Grafana** (the user-facing piece). Prometheus is internal-only (no public route).

facts.yml:
```yaml
observability:
  grafana_url: https://grafana.<domain>
  prometheus_url: http://<stack-ip>:9090
  grafana_admin_password: <from-vault>
```

## Acceptance

Observed 2026-08-08 unless noted.

- [ ] Grafana UI loads, admin login works — **half met.** `GET /api/health` returns 200
