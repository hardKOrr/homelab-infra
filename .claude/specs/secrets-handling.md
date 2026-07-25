# Spec: secrets handling

<!-- isotope:section secrets-handling:start -->

Exactly two secrets live outside Vaultwarden — `PROXMOX_API_TOKEN` and
`VAULTWARDEN_ADMIN_TOKEN` — in gitignored `config/` files or Semaphore env vars. No Ansible
Vault, ever. Everything else is generated at bootstrap, stored in Vaultwarden, and retrieved via
the `community.general.bitwarden` lookup.

## Rule

- No secret is ever written to a managed guest's filesystem, to a debug dump, or to any file
  inside the repo working tree outside gitignored `config/`. If instance metadata is persisted
  into a guest, auth keys (`api_token_secret`, `password`, tokens) are stripped first.
- Module calls whose args contain a secret set `no_log: true` (free-form dict-splat module args
  defeat this — prefer explicit params when a secret is present).
- No hardcoded credential defaults that could survive to production (e.g. `password: changeme`
  in git-managed defaults). Generate, prompt, or fail.
- Example/template files contain empty or placeholder values only.

## Enforced by

- inspection — cite this spec in findings (source: `.claude/CLAUDE.md` "Secrets" section)

<!-- isotope:section secrets-handling:end -->
