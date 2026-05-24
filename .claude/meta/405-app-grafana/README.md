# 405 — Grafana + Prometheus role + playbook

**Status:** open
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

- [ ] Grafana UI loads, admin login works
- [ ] Prometheus datasource pre-configured
- [ ] Default homelab dashboard shows data from at least one host
- [ ] node_exporter (if added to baseline) is running on all `homelab-infra` guests
- [ ] Re-run idempotent
- [ ] Decision (one app vs two) captured in `notes.md`
