# Vaultwarden recovery

Vault mode never falls back to seed files. Recovery is a deliberate two-person-style
operation: restore the external unlock material, confirm entry into Seed recovery, restore
Vaultwarden, and prove the vault before returning to normal jobs.

## Recovery material to keep outside the runner

- `/etc/rundeck/.storage-password`, or the `RUNDECK_STORAGE_PASSWORD` handover value
  emitted by runner bootstrap. This opens Rundeck Key Storage after a rebuild.
- A current PBS backup containing the Vaultwarden guest and its data directory/database.
- The automation account client ID, client secret, and master password in a separate
  recovery record. They are normally encrypted in Rundeck/Semaphore, but the only copy
  must not depend on the runner being recoverable.
- The Vaultwarden server-administration token. This is an external control-plane
  credential, not an item-vault unlock credential.
- The temporary Proxmox token and runner SSH identity needed to reach the guests.

The AES-GCM converter encrypts Key Storage at rest. A root process on the running runner
can still read the converter password and job memory; this is host protection, not a
defence against a compromised runner root account.

## Procedure

1. Stop ordinary automation. Back up the current runner and Vaultwarden guest before
   changing either one.
2. Restore `/etc/rundeck/.storage-password` as `root:rundeck` mode `0440`, restore the
   separately backed-up Key Storage database, and restart Rundeck. Do not put this
   password inside Key Storage or Vaultwarden.
3. Restore the four external Rundeck entries under
   `keys/project/homelab-infra/`: the three `vaultwarden-machine` automation credentials
   and its `admin-token`. Restore the Rundeck API token only when control-plane jobs need
   it.
4. Recreate the minimum temporary seed files and SSH identity from the recovery copies.
   Do not remove the Vault-mode marker by hand.
5. Run **Vaultwarden Recovery**, read the warning, and type
   `ENTER-SEED-RECOVERY`. That is the only maintained operation that removes the marker.
   It creates no secret and changes no guest.
6. With ordinary jobs still disabled, run Caddy and Vaultwarden explicitly with
   `LAB_SEED_MODE=1`. Prefer restoring the complete Vaultwarden guest/database from PBS;
   a blank replacement vault does not contain the platform secrets.
7. Confirm the HTTPS `/alive` endpoint, sign in as the human owner, and confirm the
   automation account can see the `homelab-infra` organization and `platform-secrets`
   collection. Never pass the owner's master password through a job.
8. Run **Vaultwarden Cutover**. It authenticates as the automation account, imports any
   restored seed values, verifies exact readback, writes the marker, and only then removes
   temporary seed files.
9. Run a non-destructive status job, then one scoped convergent deploy. A failed vault
   preflight must stop before Ansible starts.

If step 8 fails, leave Seed mode and all recovery copies intact. Do not manually create the
marker. If it succeeds, recreated seed files no longer bypass the vault.

## Non-destructive test

The repository gate exercises the marker transition, fail-closed runtime, recreated-seed
rejection, private CLI state cleanup, and child failure propagation with fake credentials.
The live recovery test stops before marker removal: verify backups, restore copies into a
temporary directory, and inspect Key Storage entry names without retrieving their values.
Never test recovery by destroying the only live copy.
