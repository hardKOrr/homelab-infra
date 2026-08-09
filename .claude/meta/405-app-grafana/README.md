# 405 — Grafana + Prometheus role + playbook

**Status:** built
**Subject:** Observability
**Related:** 401 (Ntfy), 300 (Caddy wiring), 302 (Authentik wiring)

## Goal

Bootstrap step 6. Shipped as **one combined `observability` app** — Docker-on-LXC, Prometheus
plus Grafana in one stack — rather than two separate apps: they are tightly coupled (the
Grafana datasource *is* Prometheus) and nobody runs one without the other.

Prometheus scrapes every `homelab-infra`-tagged host; Grafana ships a provisioned datasource
and a default dashboard, and its admin password is generated into the vault. Grafana is the
user-facing piece and gets Caddy, Authentik, Uptime Kuma and DNS wiring; Prometheus stays
internal with no public route.

`prometheus-node-exporter` lives in `tasks/guest-bootstrap.yml`, so every guest is scrapeable
— which is why this role ships no node-exporter container: the package already holds
`0.0.0.0:9100`.

Deployed green 2026-08-03; five of six acceptance items observed 2026-08-08.

## Remaining

- [ ] Grafana UI loads, admin login works — **half met.** `GET /api/health` returns 200 and
      `https://observability.wasitacatisaw.cc` answers 302 to its login through the estate
      wildcard, so the UI is served and reachable. Nobody has signed in; the generated admin
      password sits in the vault unused. This is the browser-credential leg 015/016 name,
      not a defect in this slice
- [x] Prometheus datasource pre-configured — the role's own verify step passes every run
- [x] Default dashboard shows data from at least one host — seven scrape targets `up`, six
      guests plus itself
- [x] node_exporter running on all `homelab-infra` guests — all seven, including the PBS
      **VM**, which does not come from the LXC path
- [x] Re-run idempotent — `changed=0` on execution 31
- [x] Decision captured — one combined app, not two

## The defect found closing this: Prometheus never saw a config change

Worth keeping in the README because it is the strongest case of the pattern in the repo.

Prometheus was scraping six of seven guests. PBS was missing and had been for five days,
even though node_exporter was running on it and `prometheus.yml` on the host had listed it
since 2026-08-03. Prometheus's *live* config — `GET /api/v1/status/config` — did not.

The compose file bind-mounted the config **file**. `ansible.builtin.template` writes a temp
file and renames it into place, so every deploy gave that path a new inode while the
container's mount stayed bound to the old one; the container went on reading a file that no
longer existed at that path. The reload handler fired on schedule, returned 200, and
reloaded the config Prometheus already had — the logs show the reload four seconds after the
write, which is exactly what a working setup looks like.

**Every scrape-target change since the container started was silently discarded**, and the
only thing that had ever delivered one was an unrelated `:latest` image pull recreating the
container. The convergence work in the same session removed that accident, which would have
frozen the target list permanently.

Fixed in `64ec867`: the config lives in its own directory and the directory is mounted.
Prometheus was the only file-to-file mount in the repo. This is "state survives boundaries
the code assumes are fresh" ([../LESSONS.md](../LESSONS.md)), and the first instance where
**every observable signal said the mechanism was working**.

## Links

- `ansible/roles/observability/`, `ansible/playbooks/apps/observability.yml`
- `ansible/vars/app-defaults/observability.yml`,
  `config.example/apps/observability.example.yml`
- `ansible/tasks/guest-bootstrap.yml` — node-exporter on every guest
- [notes.md](notes.md) — decisions, deviations, and the superseded option A/B planning text
