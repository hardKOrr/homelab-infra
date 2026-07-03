# modernize-docker-apt-repo

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** review 2026-07-02 (repo hygiene; no dedicated spec)

## Goal

Replace the deprecated `apt_key` usage in the docker role with the keyring + `signed-by` pattern
and stop hardcoding `arch=amd64`.

## Context

`ansible/roles/docker/tasks/install.yml:10-13` uses `ansible.builtin.apt_key` (deprecated;
apt-key is removed in current Debian) and lines 15-21 hardcode `deb [arch=amd64]`. Modern
pattern: download the Docker GPG key to `/etc/apt/keyrings/docker.asc` (via `get_url`, mode
0644), then the repo line
`deb [arch=<mapped arch> signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian <release> stable`.
Map architecture from `ansible_architecture` (`x86_64` → `amd64`, `aarch64` → `arm64`). The role
is Debian-only by declared project decision (CLAUDE.md, roles/docker) — keep that; do not add
distro branching. `roles/docker/tasks/config.yml` and handlers are out of scope.

## Acceptance criteria

- No `apt_key` module usage remains in `roles/docker/`.
- The repository definition uses `signed-by=` with a keyring file installed by the role.
- The architecture in the repo line derives from `ansible_architecture`, not a literal.
- The `lint` gate from `.claude/build.yml` passes on the touched files.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
