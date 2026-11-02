# Recovery and application state

## PBS datastore recovery mode

PBS instance configuration accepts `app.datastore_mode: create|reuse` (default `create`).
`reuse` requires the recovered datastore registration at the declared
`infrastructure.backups.datastore_path`, waits for serving readiness and never requests
creation/initialisation. Both modes compare registered and declared paths ignoring trailing
slashes, without changing either path. Other differences, including `.`/`..` or symlink
aliases, are not resolved. A missing/empty registered path does not equal `/`.
Default `create` also refuses a same-name registration at a genuinely different path.
Check the declaration and backing storage before retrying; this guard never relocates
or recreates the store.
This is a configuration guard, not a recovery capability declaration or independent
recovery proof. It does not attach or format external storage. See the
[PBS guide](../../roles/pbs/README.md) for supported whole-guest recovery and fail-closed
fresh-recreation limits.

## Recovery contract

`recovery:` is an explicit capability declaration in each app's defaults block. It is
merged with the instance file in the same recursive app-layer merge as `backup:` and the
app's selected `hosting.method`. A missing method is not inferred from a missing playbook:
it means
that method is not declared and a recovery request fails with a missing-capability error.

```yaml
mixpost_defaults:
  recovery:
    methods: [native]
    native:
      backup_playbook: backup-app.yml
      restore_playbook: restore-app.yml
    # project_managed:
    #   restore_role_task: restore
```

The method axis is `pbs_guest`, `native`, or `project_managed`. The destination axis is
independent and is always `new` or `existing`; all three methods accept both. A native
non-Kubernetes application plugs in by naming its product-specific backup/restore
playbook (or role task) and receives the same `instance`, `method`, `destination`,
`target`, `recovery_point`, and `overwrite` inputs as the Kubernetes implementation.

Recovery jobs use `instance`, `method`, `destination`, `target`, `recovery_point`, and
`overwrite`; `method` is required when more than one method is declared. The shared
resolver is [`tasks/recovery/resolve-method.yml`](../../tasks/recovery/resolve-method.yml),
which validates the declaration before any provider or role task runs. `restore-app.yml`
keeps `snapshot` as a compatibility alias for `recovery_point`.

Recovery reports use this shape. `state` is exactly one of `missing`,
`configured_unverified`, `stale`, `verified_fresh`, or `restore_tested`:

```json
{
  "instance": "mixpost",
  "method": "native",
  "state": "verified_fresh",
  "artifact_id": "host/mixpost/2026-08-19T03:00:00Z",
  "captured_at": "2026-08-19T03:00:00Z",
  "restore_tested_at": null,
  "exclusions": [],
  "notes": ""
}
```

`audit.py` emits the `pbs_guest` form and product paths must preserve `artifact_id` as-is.
A configured path without a captured artifact is `configured_unverified`; freshness does
not claim integrity or a successful restore. Recovery paths reuse `notify.yml` and
`assert-no-degradations.yml`, so an undelivered notification or an inactive target after
a failed restore cannot report success with a caveat.

## Application database provisioning

A SQL-consuming catalog application must declare one named backend and include `tasks/database/provision.yml` before its workload is applied. The backend-independent request is `database_provision_app_instance` plus `database_provision_config: {provider: postgresql|mariadb|mysql, instance, name, role, credential_action: reuse|rotate, postgresql_superuser: bool, extensions: []|[vector, earthdistance]}`. `reuse` and `postgresql_superuser: false` are the defaults. The task rejects an unregistered or provider-mismatched backend, a PostgreSQL privilege opt-in for another provider, and unsafe identifiers. Each service login receives the least privilege it needs to install and run; a service that needs broader privileges declares them in its request. PostgreSQL roles are non-superusers by default, and the explicit `postgresql_superuser: true` opt-in converges an existing role to superuser. MariaDB and MySQL grant the role only `ALL` on the requested database from the backend's declared client hosts. The optional PostgreSQL `vector` and `earthdistance` extensions are installed in the named application database as the PostgreSQL superuser before the consumer starts; `earthdistance` also installs its `cube` dependency. No arbitrary extension or package name is accepted. A normal rerun reuses the canonical password and converges the declared privilege; `rotate` explicitly generates, records, then applies a replacement.

Connection fields and hidden `database_password` exist only in the canonical Vaultwarden item `homelab-infra/apps/<application-instance>`; they are stored and read back before backend mutation, never enter generated facts, tracked config, or normal logs. The consumer reads that canonical item through the runtime vault contract. Redis is a cache, not a SQL provision request: consumers name a Redis instance, resolve its endpoint from `databases.<instance>`, and read that backend's password only from `homelab-infra/apps/<redis-instance>`. Current Redis has one password per instance, so consumers require distinct key prefixes and instances must not cross trust boundaries.

A consumer owns only its named database and role. Backup and restore use a named logical dump with the application stopped or otherwise application-consistent; removal requires a confirmed backup and may delete only that named database and role. It must never remove the backend guest, registry entry, other applications, or unmanaged resources. A Vaultwarden or backend failure stops the request without logging a credential; rerun `reuse` to converge, or rerun the explicit `rotate` request if a recorded rotation was interrupted.

## LiteLLM external-state contract

LiteLLM declares `hosting.method: kubernetes` and `app.database` as a named PostgreSQL consumer.
Its `app.models` list is routing configuration; a model's optional `litellm_params.api_key_env`
must name an `app.credentials` entry of `ENVIRONMENT_NAME: vaultwarden_field_name`.
Only the field name is authored in `config/apps/<instance>.yml`; the value is a hidden field
in `homelab-infra/apps/<instance>` and is copied into a namespaced runtime Secret. The role
also stores its generated stable `master_key` and `salt_key` in that same item. Neither
provider keys nor database passwords enter generated facts, ConfigMaps, PBS archives, or
normal task output.

The routing ConfigMap sets `general_settings.database_url` to an environment reference and
`store_model_in_db: false`. The external database therefore owns LiteLLM virtual keys, key
metadata, spend records and usage logs, while the ConfigMap owns model routes. LiteLLM's
native Kubernetes recovery method archives both objects' state as one PBS artifact: a custom
PostgreSQL dump and the rendered routing ConfigMap. Restore stops the target, reloads the
named database, applies the archived ConfigMap, and leaves the target stopped on failure.
