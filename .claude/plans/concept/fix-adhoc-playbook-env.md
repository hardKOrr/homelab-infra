# fix-adhoc-playbook-env

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/framework.md ("Language & toolchain", "Tests"); discovered empirically
2026-07-04 while running the fix-generate-ip-allocation-loop value-proof (related: meta slice 007
owns pinning collections in `ansible/requirements.yml` — coordinate, don't collide)

## Goal

Make ad-hoc `ansible-playbook` runs work out of the box in the gate venv: replace the removed
`stdout_callback = yaml` in `ansible/ansible.cfg` with its supported equivalent, and add `netaddr`
to `.claude/gate/requirements-dev.txt`.

## Context

Two defects block any real (non-syntax-check) playbook execution, found by actually running a
playbook in the gate venv (`~/.venvs/homelab-ansible`, collections pinned at community.general
13.1.0):

1. `ansible/ansible.cfg:6` sets `stdout_callback = yaml`, which resolves to
   `community.general.yaml`. That callback was **removed in community.general 12.0.0**; ansible
   errors out immediately with "[DEPRECATED]: community.general.yaml has been removed. The plugin
   has been superseded by the option `result_format=yaml` in callback plugin
   ansible.builtin.default from ansible-core 2.13 onwards." The two gates survive only because
   ansible-lint and `--syntax-check` never engage the stdout callback — every genuine run
   (bootstrap, app deploys, value-proof playbooks) dies on line one. This is shipped config, so it
   equally breaks any user whose control node has community.general ≥ 12.
2. `.claude/gate/requirements-dev.txt` lacks `netaddr`, so every `ansible.utils` IP filter the
   repo depends on (`ipaddr('size')` and `nthhost` in `ansible/tasks/network/generate-ip.yml`,
   `ipaddr('prefix')` in `ansible/tasks/proxmox/lxc-create.yml`) raises "Failed to import the
   required Python library (netaddr)" at runtime. It was installed manually into the venv on
   2026-07-04 as a stopgap; the requirements file must record it so a fresh venv bootstrap works.

The venv bootstrap procedure is documented in the comment block of `.claude/build.yml` (installs
`-r .claude/gate/requirements-dev.txt`, then pinned collections). The fix must keep YAML-shaped
output (the repo chose it deliberately) via the supported mechanism: `stdout_callback` pointing at
`ansible.builtin.default` with its `result_format=yaml` option (settable in `ansible.cfg` under
the callback's own section or via `ANSIBLE_CALLBACK_RESULT_FORMAT` — the groomer picks the exact
config shape and verifies the option name against the installed ansible-core's
`ansible-doc -t callback ansible.builtin.default`).

## Acceptance criteria

- `ansible/ansible.cfg` no longer references the removed `yaml` stdout callback; a trivial
  `ansible.builtin.debug` playbook runs to completion in the gate venv with **no**
  `ANSIBLE_STDOUT_CALLBACK` override, and its output is YAML-formatted.
- `netaddr` appears in `.claude/gate/requirements-dev.txt`, and an `ansible.utils.ipaddr`
  expression evaluates successfully in a playbook run from the venv.
- Both gates from `.claude/build.yml` (`lint`, `test`) still pass.
- No change to playbooks, tasks, roles, or collection pins (meta 007's territory).

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
