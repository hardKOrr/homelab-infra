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
or Vaultwarden item.

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
- A test fixture or CI artifact follows the same rule as an example/template file: a
  secret-shaped key may appear (the schema requires it), but its value must be an obvious,
  reviewable placeholder, never something that could pass for a real credential.

## Hosted CI lane policy

CI test infrastructure (see issue #29 and its children) is a second risk surface: a
fixture, workflow, or cleanup routine could leak a credential or target an unmanaged
resource even though it never touches production config.

- Every hosted `.github/workflows/*.yml` job declares explicit `permissions` — the
  repository does not rely on the default token's implicit scopes.
- A `pull_request`-triggered job never references the `secrets` context. That trigger runs
  PR-branch code with the base repository's token; injecting a secret there hands it to
  untrusted code. `push`, `workflow_dispatch`, and other trusted triggers may use secrets.
- A self-hosted runner (needed only for a future real-Proxmox acceptance lane, #35) is
  never targeted without the job also declaring `environment:`, so a required-reviewer
  approval gate stands between the trigger and the runner. GitHub's environment approval
  controls *access* to the job; it does not isolate the runner process itself, so a
  self-hosted PVE runner must additionally run on a dedicated, test-only machine, use an
  immutable reviewed SHA (never a mutable ref), and hold only test-lab credentials that
  cannot reach production.
- Cleanup in every CI lane (Docker, Kind, mock, or a future PVE lane) selects only
  resources it created itself. For Proxmox specifically that means the exact `_+lab`
  ownership tag `ansible/tasks/proxmox/README.md` documents — the same sentinel production
  removal paths already gate on — never a name prefix or heuristic guess.

## Enforced by

- `ansible/scripts/lab-run.sh` — mode guard, preflight, private CLI state, cleanup
- `ansible/scripts/secret-shape.py` and `ansible/tasks/bootstrap/write-generated-facts.yml`
- `gate/test-vaultwarden.sh` — redaction, mapping, fail-closed and cleanup tests
- `gate/check-fixture-secrets.py` and `gate/test-fixture-secrets.sh` — fixture/artifact
  secret-shape and tracked `config/` boundary
- `gate/check-workflow-policy.py` and `gate/test-workflow-policy.sh` — hosted workflow
  permissions, secrets-in-pull_request, and self-hosted-without-environment boundary
- inspection — cite this specification in findings
