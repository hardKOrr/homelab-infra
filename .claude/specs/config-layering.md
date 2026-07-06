# Spec: config layering

Three layers merged via `combine(recursive=True)` at playbook runtime:
`vars/homelabinfra-defaults.yml` → `vars/app-defaults/<app>.yml` → `config/apps/<instance>.yml`.
Users only write what differs; everything else falls through.

The authoritative data-shape contract these rules protect — namespaces, load map, the canonical
`homelabinfra_infra` shape, merge order, and per-file required keys — lives at
`ansible/vars/CONTRACT.md`; keep the two in sync.

## Rule

- Example files (`config.example/`, `vars/user-vars-example.yml`) must not teach users to blank
  out defaults: an empty-string or `0` value in an example **overrides** the git-managed default
  in `combine`. Optional keys appear commented out, never as empty values.
- Selector/bookkeeping keys that live in the config namespace (e.g. `proxmox.lxc.network`,
  `ip_address`, `stack`) never reach a module call as arguments — module args are built from an
  explicit allowlist (meta slice 003).
- Git-managed defaults files contain no null subtrees (`networks:` with no value); use `{}` or
  omit the key, and assert required subtrees with a friendly `fail_msg` at the point of use.
- One key name per concept across the whole repo (canonical: `api_host`/`api_port` — meta
  slice 004).

## Enforced by

- inspection — cite this spec in findings (source: `.claude/CLAUDE.md` "Config Hierarchy";
  meta slices 000–005 track the known violations)
