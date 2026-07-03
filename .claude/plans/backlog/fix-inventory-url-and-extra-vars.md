# fix-inventory-url-and-extra-vars

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/config-layering.md; review 2026-07-02 (related: meta 004 owns the
api_host/host key rename; meta 002 owns example-file reconciliation — this item is only the
inventory plugin's consumption of those keys)

## Context

Two defects keep the dynamic inventory from working on a fresh clone:

1. **No scheme in the URL** — `ansible/inventory/proxmox.yml:4` builds
   `url: "{{ api_host }}:{{ api_port }}"`. The `community.proxmox` inventory plugin expects a
   full URL (its default is `http://localhost:8006`), while the provisioning modules
   (`tasks/proxmox/lxc-create.yml:23`, `vm-create.yml:22`) take the same `api_host` value as a
   bare hostname. One value cannot be both. Decide the canonical shape (bare host in config is
   the smaller user surface) and derive the URL in the inventory file
   (`https://{{ api_host }}:{{ api_port | default(8006) }}`), or introduce a scheme key.
2. **Extra vars not available to the plugin** — the file templates `homelabinfra_config.*`
   (lines 4-7), which per its own comment (line 2) arrives via `-e @user-vars.yml`. Inventory
   plugin option templating only sees extra vars when `use_extra_vars` is enabled
   (`[inventory] use_extra_vars = True` in `ansible/ansible.cfg`, absent today) [unverified for
   this specific plugin version — the groomer/implementer must confirm against the installed
   community.proxmox docs once establish-ansible-gate lands].

Also note `validate_certs: false` at line 8 — acceptable for homelab Proxmox self-signed certs,
but worth a comment saying so deliberately.

## Goal

Make `ansible/inventory/proxmox.yml` consume the user's Proxmox connection config correctly: a
schemed URL and extra-vars availability for plugin option templating.

## Acceptance criteria

- The inventory `url` option resolves to a full `https://host:port` URL from the same config keys
  the modules use (no second place for users to write the host).
- `ansible/ansible.cfg` enables whatever setting the installed plugin version requires for
  extra-vars templating of inventory options, with a comment citing it — or, if the plugin
  version resolves options without it, the finding is recorded as not applicable in the Run log.
- `validate_certs: false` carries a one-line justification comment.
- The `lint` gate from `.claude/build.yml` passes on the touched files.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
