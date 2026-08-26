# Semaphore reference

Semaphore is not a supported operator interface for this repository. The live platform
uses Rundeck, and the files in this directory are not maintained at Rundeck feature parity.

`project.json` is a legacy, hand-authored Semaphore project backup. It has not been
accepted against a current Semaphore installation. It may be useful as a starting point,
but it is not a current job inventory or a tested installation path.

## What remains reusable

- The Ansible playbooks are independent of the operator interface. Start with
  [`../ansible/README.md`](../ansible/README.md).
- `ansible/scripts/semaphore-run.sh` preserves the Seed/Vault guard and runtime setup for a
  Semaphore-style checkout.
- `project.json` shows an earlier arrangement of views, surveys, repository settings, and
  environment variables.

## Known gaps

- There is no `bootstrap-semaphore.sh`.
- `project.json` does not include the current Rundeck job set or generated per-application
  maintenance jobs.
- The backup schema and import behavior have not been verified against a current Semaphore
  release.
- Secrets, runner persistence, job rendering, and reimport behavior require a new design
  and acceptance pass before Semaphore can be described as supported.

## If Semaphore support resumes

Treat adoption as implementation work, not documentation-only enablement:

1. Restore a freshly exported project backup into the selected Semaphore release.
2. Reconcile its jobs with current playbooks and the application catalog without copying
   Rundeck-specific behavior into `ansible/`.
3. Define and verify persistent `config/`, `artifacts/`, Vaultwarden credentials, runner
   SSH identity, and Proxmox access.
4. Exercise bootstrap, deploy, configure, remove, backup, restore, and failure paths.
5. Replace this notice with verified setup and operating instructions.

Until that work is complete, use [`../rundeck/README.md`](../rundeck/README.md) for the
supported operator path.
