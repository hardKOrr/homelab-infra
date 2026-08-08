# 405 — Grafana + Prometheus role + playbook

> Implemented as **option A**, one combined `observability` app
> (`roles/observability/`, `playbooks/apps/observability.yml`). The option B
> two-app split below was not taken. See notes.md.

**Status:** built — deployed green 2026-08-03 and five of six acceptance items observed live on 2026-08-08. Closing it cost the sharpest defect of that session: Prometheus could never receive a scrape-config change (see below). Decisions and deviations from the approach below are in notes.md.
**Depends on:** 401 (ntfy)
**Blocks:** metrics + dashboards story per CLAUDE.md

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
      on the guest and `https://observability.wasitacatisaw.cc` answers 302 to its login
      through the estate's wildcard certificate, so the UI is served and reachable. Nobody
      has signed in: the generated admin password sits in the vault unused. This is the
      browser-credential leg that 015/016 name, not a defect in this slice
- [x] Prometheus datasource pre-configured — the role's own
      "Verify the Prometheus datasource is provisioned" step passes in every run
- [x] Default homelab dashboard shows data from at least one host — Prometheus reports
      seven scrape targets `up`, six guests plus itself
- [x] node_exporter is running on all `homelab-infra` guests — confirmed on all seven,
      including the PBS **VM**, which is the one that does not come from the LXC path
- [x] Re-run idempotent — `changed=0` for this role on execution 31
- [x] Decision (one app vs two) captured in `notes.md` — option A, one combined app

### The defect found closing this: Prometheus never saw a config change

Prometheus was scraping six of the seven guests. PBS was missing, and had been for five
days, even though node_exporter was running on it and `prometheus.yml` on the host had
listed it since 2026-08-03. Prometheus's *live* config — `GET /api/v1/status/config` —
did not.

The compose file bind-mounted the config **file**. `ansible.builtin.template` writes a
temp file and renames it into place, so every deploy gave that path a new inode while
the container's mount stayed bound to the old one. The container went on reading a file
that no longer existed at that path. The reload handler fired on schedule, returned 200,
and reloaded the config Prometheus already had — the logs show the reload four seconds
after the file was written, which is exactly what a working setup looks like.

So **every scrape-target change since the container started was silently discarded**,
and the only thing that had ever delivered one was an unrelated `:latest` image pull
recreating the container. The convergence work in the same session removed that
accident, which would have frozen the target list permanently.

Fixed in `64ec867`: the config lives in its own directory and the directory is mounted.
Prometheus was the only file-to-file mount in the repo.

This is another instance of the pattern the index already tracks — state surviving a
boundary the code assumed was fresh — and the first where **every observable signal
said the mechanism was working**.
