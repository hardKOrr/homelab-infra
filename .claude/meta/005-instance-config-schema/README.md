# 005 — Instance config schema contradiction

**Status:** open
**Depends on:** none
**Blocks:** any real app deploy (every user will follow one example or the other)

## Problem

The two instance-config examples teach contradictory schemas:

- `config.example/apps/_template.example.yml` and `playbooks/apps/README.md` say: filename = instance name, file contains only override keys.
- `config.example/apps/radarr.example.yml` introduces top-level `app: radarr`, `instance_name: radarr`, and a nested `radarr:` block — none of which are consumed by the merge in `playbooks/apps/_template.yml:52`.

The playbook does `_app_defaults.<APP>_defaults | combine(_instance_config, recursive=True)`, so the radarr example's extra keys land in `app_config` as orphans. A user copying radarr.example.yml learns the wrong shape.

## Files

- `config.example/apps/radarr.example.yml` — strip the orphan keys, conform to template schema
- `config.example/apps/_template.example.yml` — confirm it's the authoritative example
- `ansible/playbooks/apps/_template.yml:50-52` — the merge step is the contract; keep as is

## Approach

Rewrite `radarr.example.yml` to match the template:
- Drop `app: radarr` and `instance_name: radarr` (filename does that)
- Replace nested `radarr:` block with a flat structure under `app:` (matching what the role will actually read)
- Keep it as a useful demo of overriding a couple of values

Confirm `_template.example.yml` is consistent with how the playbook merges.

## Acceptance

- [ ] `radarr.example.yml` has no top-level keys that aren't consumed by the role
- [ ] A user copying `radarr.example.yml` → `config/apps/radarr.yml` produces a working merge with no orphan keys
- [ ] Both examples teach the same shape
