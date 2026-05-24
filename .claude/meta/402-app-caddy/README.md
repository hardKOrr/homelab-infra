# 402 — Caddy role + playbook

**Status:** open
**Depends on:** 300 (wiring), 401 (ntfy must exist so Caddy can notify on cert renewal)
**Blocks:** 500 (bootstrap), every app deploy that wires through Caddy

## Problem

Caddy is bootstrap step 3 (reverse_proxy.provider: caddy default). No role or playbook exists.

Native LXC deployment.

## Files

To create:
- `ansible/roles/caddy/{tasks,handlers,defaults,meta,templates,files}/...`
- `ansible/playbooks/apps/caddy.yml`
- `ansible/vars/app-defaults/caddy.yml`
- `config.example/apps/caddy.example.yml`

## Approach

1. Install via official Caddy apt repo.
2. Configure the admin API to listen on the LXC's interface (not just localhost) so wiring tasks can hit it from the Ansible controller.
3. Template `/etc/caddy/Caddyfile` with **only**:
   - The `admin <ip>:2019` directive
   - Global TLS email
   - Empty route table (routes get added at runtime via the admin API by wiring tasks)
4. systemd enable + start.
5. Call `write-generated-facts`:
   ```yaml
   caddy:
     admin_api_url: http://<lxc_ip>:2019
     host_ip: <lxc_ip>
   ```

Decision: should Caddy use ACME-DNS-01 vs HTTP-01? For homelab with public DNS, HTTP-01 is simplest (Caddy auto-issues). For internal-only labs, we'd need DNS-01 with the user's DNS provider — out of scope for v1, document as future work.

Implement `lab-*` scripts.

## Acceptance

- [ ] Caddy admin API reachable from the controller
- [ ] `curl http://<caddy-ip>:2019/config/` returns the empty config
- [ ] First wired app gets a valid TLS cert via HTTP-01
- [ ] facts.yml has `caddy.admin_api_url`
- [ ] Re-run is idempotent
