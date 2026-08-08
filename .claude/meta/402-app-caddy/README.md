# 402 — Caddy role + playbook

**Status:** done — deployed live 2026-08-03, all acceptance items observed 2026-08-08. Two of them were met by a different mechanism than the criterion named (DNS-01 wildcard rather than per-hostname HTTP-01; the role-keyed `reverse_proxy` registry key rather than `caddy.admin_api_url`) — see Acceptance. Decisions and deviations from the approach below are in notes.md.
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

All observed 2026-08-08 from the runner unless noted.

- [x] Caddy admin API reachable from the controller — `GET http://192.168.0.10:2019/config/`
      returns 200
- [x] `/config/` returns the running config — it is no longer empty, which is the
      stronger result: it returns the live route set, each route carrying its `@id`,
      its upstream dial address and its `remote_ip` range
- [x] A wired app gets a valid TLS certificate — **via DNS-01, not HTTP-01 as written.**
      All six estate hostnames serve `CN=*.wasitacatisaw.cc`, Let's Encrypt issuer YE2,
      valid to 2026-11-04, and every one verifies (`ssl_verify_result 0`). The wildcard
      model is slice 015; the per-hostname model this criterion assumed is gone
- [x] facts.yml records the endpoint — **the criterion names a key that no longer
      exists.** Slice 200 made the registry role-keyed, so it is `reverse_proxy` with
      `host`, `port`, `provider`, `instance` and `internal_cidrs`, not
      `caddy.admin_api_url`. Recorded as met against the shipped shape
- [x] Re-run is idempotent — `changed=0` on the caddy host across executions 30 and 31
