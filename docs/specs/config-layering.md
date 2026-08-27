# Spec: config layering

Configuration uses two separate recursive merges:

- Platform input: `ansible/vars/homelabinfra-defaults.yml` → `config/proxmox.yml` →
  `config/infrastructure.yml` → the optional legacy `user_vars_file`.
- Application input: `ansible/vars/app-defaults/<app>.yml` →
  `config/apps/<instance>.yml`.

Users write only what differs. Application defaults never merge into
`homelabinfra_config`; an application playbook builds `app_config` separately.

The authoritative data-shape contract these rules protect — namespaces, load map, the canonical
`homelabinfra_infra` shape, merge order, and per-file required keys — lives at
`ansible/vars/CONTRACT.md`; keep the two in sync.

## Rule

- Example files (`config.example/`, `ansible/vars/user-vars-example.yml`) must not teach users to blank
  out defaults: an empty-string or `0` value in an example **overrides** the git-managed default
  in `combine`. Optional keys appear commented out, never as empty values.
- Selector/bookkeeping keys that live in the config namespace (e.g. `proxmox.lxc.network`,
  `ip_address`, `stack`) never reach a module call as arguments — module args are built from an
  explicit allowlist.
- Git-managed defaults files contain no null subtrees (`networks:` with no value); use `{}` or
  omit the key, and assert required subtrees with a friendly `fail_msg` at the point of use.
- Use one key name per concept across the repository. The canonical Proxmox keys are
  `api_host` and `api_port`.

## Enforced by

- inspection — cite this specification and `ansible/vars/CONTRACT.md` in findings
