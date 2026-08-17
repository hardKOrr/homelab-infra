# Vaultwarden recovery

Vault mode never falls back to seed files. Recovery is a deliberate two-person-style
operation: restore the external unlock material, confirm entry into Seed recovery, restore
Vaultwarden, and prove the vault before returning to normal jobs.

## Recovery material to keep outside the runner

- `/etc/rundeck/.storage-password`, or the `RUNDECK_STORAGE_PASSWORD` handover value
  emitted by runner bootstrap. This systemd EnvironmentFile opens both Rundeck Key Storage
  and its encrypted project configuration after a rebuild; restoring only one converter
  namespace leaves jobs visible but unable to start.
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
   separately backed-up Rundeck database, and confirm both
   `rundeck.storage.converter.1` and `rundeck.config.storage.converter.1` select
   `passwordEnvVarName=RUNDECK_STORAGE_PASSWORD`. The file is a systemd EnvironmentFile;
   it contains `RUNDECK_STORAGE_PASSWORD=<value>`. Do not put this password inside Key
   Storage or Vaultwarden.
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
   automation account is a confirmed Admin of the `homelab-infra` organization. Cutover
   recreates the `platform-secrets` collection if the restore did not carry it. Never pass
   the owner's master password through a job.
8. Run **Vaultwarden Cutover**. It authenticates as the automation account, imports any
   restored seed values, verifies exact readback, writes the marker, and only then removes
   temporary seed files.
9. Restore the non-secret `BW_SERVER` value in `/etc/homelab-infra/lab-run.env`, then run a
   non-destructive status job and one scoped convergent deploy. A failed vault preflight
   must stop before Ansible starts.

If step 8 fails, leave Seed mode and all recovery copies intact. Do not manually create the
marker. If it succeeds, recreated seed files no longer bypass the vault.

## Recovery acceptance

The repository gate exercises the marker transition, fail-closed runtime, recreated-seed
rejection, private CLI state cleanup, both Rundeck converter namespaces, and child failure
propagation with fake credentials.

Live acceptance completed 2026-08-17 without changing the production guest. A fresh runner
at VMID `168000203` restored credential-free config plus the external recovery inputs from
PBS archive `backup/ct/168000003/2026-08-17T16:49:21Z`. Config Doctor execution 243 passed
Vaultwarden preflight, retained the production SSH public identity, and removed the
execution-private key afterward. A full isolated restore passed the same check as execution
242. Both scratch guests were then destroyed. See slice 014 notes for the defects found.
