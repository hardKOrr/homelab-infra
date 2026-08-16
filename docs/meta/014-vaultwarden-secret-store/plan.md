Work in the homelab-infra repository and implement the Vaultwarden secret-storage path end to end. Do not use Isotope.

Context:

- Read `docs/meta/INDEX.md`, `docs/meta/014-vaultwarden-secret-store/README.md`, `docs/specs/secrets-handling.md`, `ansible/vars/CONTRACT.md`, `rundeck/README.md`, and the current Vaultwarden/bootstrap implementation first.
- Preserve all existing uncommitted work. Some Vaultwarden documentation reconciliation may already be present; inspect it rather than reverting it.
- The live lab currently has:
  - Rundeck at `http://192.168.0.2:4440`
  - Vaultwarden 1.37.1 at `192.168.0.10`
  - The complete baseline platform deployed and convergent
- Never request or print passwords, tokens, vault sessions, or Key Storage encryption keys in chat or logs.

Critical correction to the existing design:

- `VAULTWARDEN_ADMIN_TOKEN` only administers the Vaultwarden server. It cannot decrypt or manipulate vault items.
- Slice 014 therefore requires a real, dedicated Vaultwarden automation account. Do not use the human owner’s personal account for jobs.
- The supported unattended flow is the Bitwarden CLI personal API-key flow:
  - `BW_CLIENTID`
  - `BW_CLIENTSECRET`
  - `BW_PASSWORD`
  - `bw login --apikey`
  - `bw unlock --passwordenv BW_PASSWORD --raw`
  - ephemeral `BW_SESSION`
- The API key authenticates but does not replace the master password used to decrypt the vault.
- Do not assume Vaultwarden supports Bitwarden Secrets Manager machine accounts.

Desired model:

1. Human owner enrollment

   A new lab must not deploy a permanently empty Vaultwarden with registration disabled and no documented path forward.

   Add an explicit first-owner enrollment workflow:
   - Collect an owner email during bootstrap, with a noninteractive environment-variable equivalent.
   - Keep public signups disabled.
   - Use the Vaultwarden admin facility to invite that exact address.
   - Give the operator the HTTPS registration URL.
   - The operator chooses the master password directly in Vaultwarden; it must never pass through Ansible, Rundeck, config, or logs.

2. Dedicated automation principal

   Define a one-time enrollment/cutover ceremony for a dedicated account such as `homelab-infra@<lab-domain>`:
   - Create or invite the account.
   - Create a `homelab-infra` organization and a restricted `platform-secrets` collection, or establish an equally recoverable supported arrangement.
   - Give both the human owner and automation principal the minimum appropriate access.
   - Obtain the automation account’s personal API client ID and secret.
   - Stage `BW_CLIENTID`, `BW_CLIENTSECRET`, and `BW_PASSWORD` securely.
   - Clearly identify which portions inherently require a human and do not pretend they can be derived from the admin token.

3. Encryption at rest

   Store the three automation credentials as individual Rundeck Key Storage password entries under project scope, for example:

   - `keys/project/homelab-infra/vaultwarden-machine/client-id`
   - `keys/project/homelab-infra/vaultwarden-machine/client-secret`
   - `keys/project/homelab-infra/vaultwarden-machine/master-password`

   Configure Rundeck’s AES-256-GCM storage converter for the entire `/keys` tree, not only these three entries, so the existing Proxmox token and SSH key are protected too.

   The Key Storage encryption password:
   - Must not be committed or stored inside Rundeck Key Storage.
   - Should be supplied through a root-only file, systemd credential, or a stronger locally available mechanism.
   - Needs a documented, separately backed-up recovery copy.
   - Must not be placed only inside Vaultwarden, which would create a circular dependency.

   Verify the exact capabilities of the installed Rundeck OSS version. The current repo states that ordinary script steps cannot consume Key Storage values without per-job secure options. Do not hand-wave around that limitation:
   - Find and implement a supported secure injection mechanism.
   - Centralize it as much as Rundeck permits.
   - Do not duplicate secrets into `/etc/homelab-infra/secrets.env`.
   - Do not put secrets in command-line arguments or ordinary job options.
   - Provide Semaphore-equivalent secret-variable handling.

4. Vault runtime preflight

   Install and configure the official Bitwarden CLI on the runner.

   Add a common preflight used by every mutating deploy:
   - Configure the Vaultwarden server URL.
   - Authenticate with the personal API key.
   - Unlock using `BW_PASSWORD`.
   - Hold `BW_SESSION` only in memory for the execution.
   - Synchronize and resolve every required item before infrastructure mutation begins.
   - Use `no_log: true` around all secret-bearing Ansible operations.
   - Lock/logout and clear temporary state in an `always` cleanup path.
   - Fail closed before mutation if Vaultwarden is unreachable, authentication fails, unlock fails, or required items are missing.

   Avoid persistent CLI state containing usable sessions. Use an execution-private temporary directory with restrictive permissions and guaranteed cleanup where the CLI requires state.

5. Secret reads and writes

   Replace the `ansible/tasks/bitwarden/todo/` stubs with real reusable tasks for:
   - authentication/unlock;
   - required-item lookup;
   - canonical item upsert;
   - readback verification;
   - cleanup/lock/logout.

   Audit the repository to enumerate every generated or consumed credential. Do not trust stale documentation.

   Use canonical organization-owned items/fields. The intended role-based grouping in slice 014 is a starting point:
   - `homelab-infra/sso`
   - `homelab-infra/notifications`
   - `homelab-infra/monitoring`
   - `homelab-infra/metrics`
   - `homelab-infra/backups`
   - `homelab-infra/dns`
   - `homelab-infra/vaultwarden`
   - one item per media/application instance where needed

   Writes must:
   - store generate-before-apply secrets before changing the target service;
   - store create-once API-returned secrets immediately;
   - verify exact readback without logging values;
   - fail the deployment if storage or verification fails;
   - define compensation for create-once secrets whose vault write fails, preferably immediate revocation when supported.

6. Cutover and migration

   Implement an explicit durable cutover marker under `/etc/homelab-infra/`.

   Seed mode:
   - Exists only for fresh bootstrap or explicit recovery.
   - May use the minimum temporary files necessary to deploy and enroll Vaultwarden.
   - Must remain resumable until every imported secret has been verified by readback.

   Vault mode:
   - Every ordinary mutating job requires Vaultwarden preflight.
   - Recreated seed files must not bypass Vaultwarden.
   - Vault failure must never silently fall back to local secret files.

   Migrate every existing generated credential out of `config/.generated/facts.yml`.
   After cutover:
   - `facts.yml` contains topology and non-secret registry data only.
   - Reject token-, password-, private-key-, API-key-, API-secret-, and credential-shaped fields.
   - Remove normal-job reliance on `/etc/homelab-infra/secrets.env`.
   - Remove the generated Vaultwarden-token file only if the final bootstrap/recovery design safely provides its required external credential elsewhere. Remember that Vaultwarden cannot contain its own bootstrap credential.
   - Do not delete the only copy of any secret until its vault write and readback are proven.

7. Recovery

   Add an explicit recovery workflow and runbook:
   - No automatic re-entry into seed mode.
   - Explain restoring the runner’s Key Storage encryption key.
   - Explain restoring the automation account credentials.
   - Explain recovering Vaultwarden itself and then restoring normal Vault mode.
   - Test the claimed recovery path without destroying the only live copies.
   - Account for PBS backups and restoration of the Vaultwarden data directory/database.

8. Documentation and status

   Reconcile README, CLAUDE.md, the secrets spec, CONTRACT.md, config examples, Rundeck docs, and slice 014 with the implementation.

   Be precise about:
   - human owner versus automation account;
   - Vaultwarden admin token versus user-vault credentials;
   - encryption at rest versus protection from root on a running runner;
   - current implementation versus target state.

   Do not mark slice 014 done until its acceptance criteria are actually observed. Record local verification and live verification separately. Do not close unrelated slices merely because the general bootstrap ran.

Execution requirements:

- Start by auditing the current worktree and current secret flows.
- Produce a concise implementation plan, then implement it; do not stop at design.
- Make safe assumptions where possible, but stop for human input where account registration, master-password choice, API-key creation, or a destructive live cutover inherently requires it.
- Never expose secrets in command output.
- Use `apply_patch` for edits.
- Run `git diff --check`, `gate/lint.sh`, and `gate/test.sh`.
- Add focused tests for secret redaction, fail-closed preflight, seed/cutover behavior, item mapping, cleanup, and absence of secrets from generated facts.
- Before live migration, inventory exactly which secret-bearing fields exist and present a non-secret migration manifest for approval.
- Do not perform destructive live migration, delete seed files, rotate credentials, or alter the only recovery copies without explicit approval.