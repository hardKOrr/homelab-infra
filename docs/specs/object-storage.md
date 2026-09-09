# Object storage

Object storage used by an application must have a named owner and a documented recovery
boundary. An application may either consume a named platform storage instance or own a
local storage service inside its multi-container recovery unit. The choice must be explicit
in `ansible/vars/app-defaults/<app>.yml`; an app must not silently create a sidecar that
looks like a shared backend.

Credentials, access keys, and secret keys are generated or read from the canonical
Vaultwarden item `homelab-infra/apps/<instance>`. They do not belong in `config.example/`,
tracked defaults, generated topology facts, Compose templates, or normal task output.

For Plane Batch C, `plane-minio` is the named local storage owner and its persistent
`object_storage_path` is part of the Plane Compose recovery unit. The PostgreSQL and Redis
backends remain independently managed named services; removing Plane must not remove either
shared backend.
