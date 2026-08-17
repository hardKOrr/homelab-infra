# Spec: secrets handling

No Ansible Vault, ever. There are two explicit phases:

- **Seed mode** may read temporary Proxmox, runner SSH, and Vaultwarden-admin material only
  for Caddy/Vaultwarden enrollment, cutover, or confirmed recovery.
- **Vault mode** requires the dedicated Vaultwarden automation account before any mutating
  playbook starts. No vault means no deploy, and recreated seed files are ignored.

The automation account uses `BW_CLIENTID`, `BW_CLIENTSECRET`, and `BW_PASSWORD`; the API
key authenticates the client but the master password decrypts the vault. Its ephemeral
`BW_SESSION` and private CLI state are removed after every execution. The Vaultwarden
admin token is a separate external server-control credential and cannot read items.

Rundeck stores those four external values under project Key Storage protected by the
AES-256-GCM converter for `/keys`. The same converter protects encrypted project
configuration. Both namespaces read one separately backed-up value from the root-owned
systemd EnvironmentFile `/etc/rundeck/.storage-password`; that value is never a Key Storage
or Vaultwarden item. Semaphore uses encrypted secret variables for the equivalent inputs.

## Rule

- No secret is written to generated facts, managed-guest metadata, a debug dump, a job
  artifact, or a tracked file. Temporary seed files are removed only after exact vault
  readback succeeds.
- Generated facts are topology-only. Their writer rejects token-, password-, secret-,
  private-key-, API-key-, credential-, and ARL-shaped keys.
- Generate-before-apply values are written and read back before service mutation.
  Create-once API values are stored immediately; where supported, failed storage revokes
  the newly created credential.
- Module calls whose args contain a secret set `no_log: true` (free-form dict-splat module args
  defeat this — prefer explicit params when a secret is present).
- No hardcoded credential defaults that could survive to production (e.g. `password: changeme`
  in git-managed defaults). Generate, prompt, or fail.
- Example/template files contain empty or placeholder values only.

## Enforced by

- `ansible/scripts/lab-run.sh` — mode guard, preflight, private CLI state, cleanup
- `ansible/scripts/secret-shape.py` and `ansible/tasks/bootstrap/write-generated-facts.yml`
- `gate/test-vaultwarden.sh` — redaction, mapping, fail-closed and cleanup tests
- inspection — cite this spec in findings (source: `AGENTS.md` "Secrets" section)
