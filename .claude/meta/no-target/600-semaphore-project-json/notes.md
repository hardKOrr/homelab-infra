# 600 — notes

## 2026-07-25 — implementation

`semaphore/project.json` written by hand in Semaphore's **project backup** format (the
Projects → Restore / `POST /api/projects/restore` shape), with 14 templates across three
views. `semaphore/README.md` rewritten around it. JSON validity is verified; the schema
itself is not — see below.

### Decisions

**Option (A) from the README: one template per app.** Each Deploy template hard-codes
`-e instance=<app>` in `arguments`, so a deploy is one click with nothing to type — the
one-click promise, literally. Second instances (`radarr-4k`) are a template copy, which
is the same work as adding an app.

**`ANSIBLE_ROLES_PATH=ansible/roles` in the environment.** Semaphore runs from the repo
root; `ansible/ansible.cfg` sets `roles_path = roles/` relative to `ansible/`, so
without this every `include_role` fails. This is the one non-obvious import requirement
and it is called out in the README table.

**Secrets are blanks, by design.** A Semaphore backup carries no secret material, so the
SSH key is created empty and the Proxmox variables are present-but-blank. The README
lists the three post-import fill-ins (repo URL, SSH key, environment).

**No Wire Stack template.** `playbooks/stacks/wire-<stack>.yml` does not exist (slice
504). A template pointing at a missing playbook is worse than an absent one.

### Open risk

**The backup schema is reconstructed, not exported.** Semaphore's restore format is
versioned with the server, and this file was written from the documented shape rather
than dumped from a running instance. Field names most likely to drift: `survey_vars`
entry keys, `cron`, and the by-name references from a template to its repository /
inventory / environment / view. If restore rejects it, the fastest fix is to import
what does load, finish the project in the UI, then `GET /api/project/<id>/backup` and
commit the server's own output over this file.

### What live acceptance must confirm

Every acceptance box: a clean restore into a fresh Semaphore, all 14 jobs visible, each
one running against a populated `config/`.
