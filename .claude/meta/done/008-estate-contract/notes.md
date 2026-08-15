# 008 — notes

## 2026-08-15 — the second estate, and the three defects declaring it found

The estate contract shipped 2026-07-25 and was gate-green for three weeks. Nothing had
ever run it. Declaring `foxglove` (foxglove-collective.com) alongside `personal`
(wasitacatisaw.cc) took eleven Rundeck executions, 146 through 161, and produced **three
defects, all of them in code both gates passed**. None is exotic; each is what
single-instance code looks like when a second instance arrives.

### The inert half was genuinely inert

Worth saying first, because it is the part the contract promised and the part that held.
Declaring a `domains:` map with a default estate changed **nothing** for an app without
`routing.estate`: execution 146 (`Deploy Ntfy`) reported `changed=0` on both hosts, and
`config/.generated/facts.yml` was byte-identical to the copy taken before the run. The
undeclared-estate assert fired with the message it promised, and the overlay replaced
`sso` whole — the default estate's Authentik host did not survive into the second estate.

### Defect 1 — the estate's Authentik took over the platform's credentials

`roles/authentik` stored every deployment under the fixed vault item `homelab-infra/sso`
and read its API-token continuity from the equally unscoped `homelabinfra_infra.sso.token`.
Execution 151 deployed the estate's Authentik **green, `failed=0`**, and in doing so:

- wrote the new instance's host, admin password, Postgres password and secret key **over
  the platform's own vault item**, and
- handed the new instance **the platform Authentik's API token** — two independent
  identity providers accepting one credential. Confirmed rather than inferred: the
  SHA-256 of `AUTHENTIK_BOOTSTRAP_TOKEN` in each host's `.env` was the same twelve hex
  characters. After the fix they differ.

The non-secret half of the same information was *already* estate-scoped by
`write-generated-facts.yml`, which is what made the naming look deliberate. Fixed in
`b68a117`: the item is `homelab-infra/estates/<name>/sso` for an estate instance, and the
continuity read uses the same scope, so a second instance cannot inherit the first's
identity. `vault-runtime.py` already mapped three-segment estate items into
`infra.estates.<name>.<role>`, so no plumbing changed.

### Defect 2 — the estate had no token, so its first app could not wire

The direct consequence of defect 1. `resolve-estate.yml` replaces `sso` whole from
`estates.<name>.sso`, whose token lives in the vault item that was never written under
that name. Execution 152 therefore failed the wiring contract assert
(`homelabinfra_infra.sso.token | length > 0`) — correctly, and with the right message.
The assert did its job; the naming underneath it did not.

### Defect 3 — `delete_data: true` did not delete the data

Removing the estate's Authentik to redeploy it cleanly (execution 153) reported success
having left `/opt/authentik/data` — including the whole Postgres cluster — on disk.
`Remove App`'s docker branch deleted the Compose project directory `/opt/<instance>` and
any *named* volumes; the data path is a **bind** mount and, for the older app defaults,
was keyed by app name rather than instance, so it sat outside everything removal touched.

The next deploy (155) then generated a fresh Postgres password, and the surviving database
refused it: sixty failed readiness retries, and nothing in the Ansible output naming the
cause — the reason was only visible in `docker logs`, as
`password authentication failed for user "authentik"`. Fixed in `9c4393e`, which deletes
bind-mounted data and config paths in the docker branch and keys every app default by
`{{ instance }}` the way the newer apps already did. For every instance deployed today the
new path resolves to the identical directory, because each is named after its app.

### What it cost, and the one thing that would have caught it

Eleven executions, of which six were failures or recovery runs. Both gates were green at
every point, including on the commit that shipped the fixed-name vault item. The fix's own
expressions were proven offline before pushing — four fact shapes including "named estate
with nothing recorded yet, must NOT inherit the default estate's token" — which is cheap
and worth doing for anything an estate makes multi-instance.

### Left behind on purpose

- The estate's Authentik (`authentik-foxglove`, LXC 168000200, its own
  `sso_stack_foxglove` host, 4 cores / 4 GB / 32 GB) is **still running and empty** — no
  applications, since the test app was removed at the end. Sizing is declared in the
  runner's `config/infrastructure.yml` under `stacks:`.
- The estate's Cloudflare DNS-01 token is authored in the runner's
  `config/infrastructure.yml`, not in Vaultwarden, because the cutover job refuses to run
  post-cutover (INDEX records this).
- **No LAN DNS for the estate.** The operator scoped DNS wiring to foxglove only, and
  `estates.foxglove.dns` is not populated, so `*.foxglove-collective.com` does not resolve
  on the LAN. TLS is unaffected — DNS-01 validates from the public internet — but reaching
  an estate app by name from a browser needs that record.
