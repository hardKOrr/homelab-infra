# Configuration examples

This directory documents the user-owned files that live under the repository-root
`config/` directory on the runner. The examples contain no live values and are safe to
track. The resulting `config/` directory is runtime state and must remain untracked.

Rundeck bootstrap writes the initial configuration. Use these examples when reviewing the
available settings or when preparing configuration manually.

## Files

- `proxmox.yml` documents the Proxmox connection, placement, storage, and network shape.
- `infrastructure.yml` documents platform services, providers, domains, and shared policy.
- `apps/<app>.example.yml` documents optional overrides for one application instance.
- `apps/_template.example.yml` is the starting point for an application that does not yet
  have an example.

The authoritative schema and merge behavior are in
[`../ansible/vars/CONTRACT.md`](../ansible/vars/CONTRACT.md). Application defaults live in
`ansible/vars/app-defaults/`. An instance file should contain only values that differ from
those defaults.

## Safety

- Do not put credentials or generated secret values in an example file.
- Do not commit files from the repository-root `config/` or `artifacts/` directories.
- Store post-cutover secrets through the Rundeck **Store Secret** job.
- Use an application's **Configure** job for normal instance overrides. Use these examples
  as the field reference, not as a second configuration source.

When a configuration field changes, update its example and the authoritative contract in
the same change. Run the checks selected by [`../gate/README.md`](../gate/README.md).
