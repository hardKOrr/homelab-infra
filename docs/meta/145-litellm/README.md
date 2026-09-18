# 145 — Batch C: deploy LiteLLM

**Status:** built
**Subject:** LiteLLM on the Kubernetes hosting backend with external durable state
**Related:** 046 (shared Kubernetes storage), [closed catalog issue #408](https://github.com/hardKOrr/homelab-infra/issues/408), [observation issue #224](https://github.com/hardKOrr/homelab-infra/issues/224)

## Goal

Deploy LiteLLM Proxy as an independently managed Kubernetes instance without a pod-local
state directory. The generated routing configuration is a namespaced ConfigMap, provider
credentials and stable proxy/master and salt keys are runtime copies of Vaultwarden fields,
and virtual keys, key metadata, spend records and usage logs are held in a named external
PostgreSQL database. The role's PBS backup archives the rendered routing configuration and
an application-consistent PostgreSQL dump together; restore is plan-first and restores both
into a target namespace.

The hosting decision is **Kubernetes**, not `ai_stack` Docker: LiteLLM's official proxy
configuration supports an external `database_url`, its proxy health readiness reports the
database connection, and its provider routes can reference environment variables rather
than embedding provider keys. A pod therefore has no durable filesystem requirement. The
repository deliberately sets `store_model_in_db: false`, so model routing remains in the
versioned/runtime configuration artifact while database state covers keys and usage. The
opt-in shared NFS class is not needed for this design.

Upstream references: [LiteLLM proxy quick start](https://docs.litellm.ai/docs/proxy/docker_quick_start),
[LiteLLM health endpoints](https://docs.litellm.ai/docs/proxy/health), and the
[LiteLLM proxy configuration example](https://docs.litellm.ai/docs/proxy/config_settings).

## Remaining

- [x] Repository implementation, Vaultwarden-only credential boundary, external PostgreSQL
      state, backup/restore mechanism, wiring and removal are covered by the code and gates.
- [x] Synthetic fixture verification covers the Kubernetes kind, external database, secret
      references, readiness probes, backup/restore and operator job surface.
- [ ] Live evidence remains deferred: name the LiteLLM instance, Kubernetes cluster,
      PostgreSQL backend, provider accounts and disposable target before execution; run the
      deploy, health check, rerun, recovery and removal scenario under the
      self-contained real-Proxmox observation issue #224. Revoke any test provider keys after acceptance.
      No live target is authorized or changed by this repository slice.

## Links

- `ansible/vars/app-defaults/litellm.yml` — hosting, external database, credential map and backup contract
- `ansible/roles/litellm/` — ConfigMap/Secret deployment and PostgreSQL + routing-config recovery jobs
- `ansible/playbooks/apps/litellm.yml` — database provisioning, Kubernetes deploy, registry and wiring
- `config.example/apps/litellm.example.yml` — non-secret route and Vaultwarden field example
- `catalog/applications.yml`, `rundeck/jobs/deploy-litellm.yaml` — operator catalog and deploy job
- `gate/test-litellm-contract.sh` — synthetic contract checks
