# 200 — Implement write-generated-facts

**Status:** open
**Depends on:** 004 (proxmox key naming)
**Blocks:** 201, 202, all wiring slices (300-305), all app slices (400+)

## Problem

`tasks/bootstrap/write-generated-facts.yml` is a TODO header only. Every wiring task and every app deploy reads `homelabinfra_infra` from `config/.generated/facts.yml`, so nothing downstream can work until this exists.

## Files

- `ansible/tasks/bootstrap/write-generated-facts.yml` — implement
- (no other files change — this is a leaf task consumed by bootstrap.yml plays)

## Approach

Single task file that takes two inputs and appends/merges to `config/.generated/facts.yml`:

- `generated_facts_service` — top-level key under which the data lands (e.g. `vaultwarden`, `caddy`, `notifications`)
- `generated_facts_data` — dict to deep-merge under that key

Steps:
1. Ensure `config/.generated/` exists (create with mode 0700 — contains secrets).
2. Read existing `facts.yml` if present; default `{}` if not.
3. Deep-merge `{ generated_facts_service: generated_facts_data }` into it.
4. Write back as YAML with restricted mode (0600).

Use `ansible.builtin.copy` with `content: "{{ existing | combine({key: data}, recursive=True) | to_nice_yaml }}"`. Read existing via `lookup('file', path, errors='ignore') | from_yaml | default({}, true)`.

## Acceptance

- [ ] First call (file absent) creates `config/.generated/facts.yml` mode 0600 with the supplied data
- [ ] Second call with a different `generated_facts_service` appends without losing the first
- [ ] Third call with the same `generated_facts_service` but different nested data deep-merges
- [ ] File is valid YAML loadable by `include_vars`
