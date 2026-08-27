# Spec: framework

The repo's dialect — how Ansible is written *here*. Filled from observed code; cite this spec in
findings. Contract-level rules live in the sibling specs this file links to.

## Language & toolchain

- Ansible (YAML) throughout. `gate/requirements-dev.txt` pins the gate's Python toolchain;
  `ansible/requirements.yml` is the single source of truth for Galaxy collection pins.
- Gates run inside a WSL venv (`~/.venvs/homelab-ansible`) via the committed wrappers
  `gate/lint.sh` / `gate/test.sh`.
  Never replace the wrappers with an inline one-liner through the Windows→WSL relay — quoting
  hazards can silently run zero iterations and exit 0 (rationale in `gate/README.md` and
  the scripts).
- The repo lives on NTFS under `/mnt/c`: Ansible's world-writable-cwd check silently ignores a
  cwd-relative `ansible.cfg`, so anything running Ansible from WSL must export `ANSIBLE_CONFIG`
  to the absolute path (the gate scripts do; copy the pattern).
- Any new script under `gate/` must be forced to LF in `.gitattributes` — a CRLF shebang
  breaks `bash` in WSL.

## Tests

- `gate/test.sh` syntax-checks the selected playbooks without contacting Proxmox, then runs
  focused regression checks. `gate/README.md` owns the current scope and invocation details.
- Verification beyond the gate is proportional to the changed behavior. Record evidence with
  the work item that requires it.

## Lint

- `gate/lint.sh` owns the lint pipeline; `gate/README.md` documents its scope. The Ansible
  ruleset lives in `ansible/.ansible-lint`. Never add a `skip_list` entry without documenting
  the concrete reason on that line. `**/todo/` staging directories are not deliverable code.

## Errors

- Assert-first: a task file opens with `ansible.builtin.assert` naming its required inputs with a
  friendly `fail_msg` (`ansible/tasks/proxmox/lxc-create.yml:3`).
- Absent provider/feature is a silent no-op via `when:` (see
  [provider-noop-wiring](provider-noop-wiring.md)); hard-fail is reserved for contract violations
  (missing required input, DHCP without an explicit `vmid`).

## Naming & layout

- FQCN for every module: `ansible.builtin.set_fact`, `community.proxmox.proxmox` — never short
  names.
- Task files are kebab-case verb-object (`ip-to-vmid.yml`, `find-or-create-host.yml`), one
  concern per file. Per-item logic splits into a companion file driven by `include_tasks` +
  `loop_control.loop_var` (`ip-to-vmid.yml` dispatching `ip-to-vmid-guest.yml`).
- `import_tasks` for static composition in playbooks; `include_tasks` when looping or dynamic.
- Every task file carries a header comment: what it does, its inputs/outputs, and whether it is
  no-arg or takes documented selector vars (`network_name`, `guest_type`).
- Long Jinja expressions use `>-` folded scalars, not one-line strings.

## Review reflexes

Repo-typical defects the reviewer checks in every diff, each owned by a spec:

- Bare `set_fact` on a namespace dict, or `default(omit)` stored into a fact →
  [namespace-merge-discipline](namespace-merge-discipline.md)
- Fact-sourced values in arithmetic/comparison without inline `| int`; `~` vs `|` precedence →
  [jinja-type-discipline](jinja-type-discipline.md)
- Secrets in dict-splat module args (defeats `no_log`), debug dumps, or files written to guests →
  [secrets-handling](secrets-handling.md)
- Empty-string/`0` values in example files that would override git-managed defaults in `combine` →
  [config-layering](config-layering.md)
- Plays targeting `hosts: proxmox_nodes` with `run_once` facts — provisioning runs on
  `localhost`, and only node-local `pct`/`qm` operations are delegated (architecture
  "Proxmox boundary" seam).

## Enforced by

- `gate/lint.sh` + `gate/test.sh`; everything else by
  inspection — cite the linked spec in findings.
