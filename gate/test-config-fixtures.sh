#!/usr/bin/env bash
# Focused regression tests for configuration loading, layering, and provider no-op
# wiring — issue #30. Everything here runs against the tracked synthetic fixtures in
# gate/fixtures/config/ (see gate/fixtures/README.md), never against config/. No
# playbook is executed against a real or dynamic inventory and nothing touches
# Vaultwarden, Proxmox, or the network.
#
# 1. config-doctor.sh accepts the valid fixture and names every problem in the
#    invalid one, by file and key path, exactly as ansible/vars/CONTRACT.md §5
#    requires.
# 2. The homelabinfra_config recursive-merge expression in
#    ansible/tasks/load-user-vars.yml is lifted out and rendered — the same
#    technique gate/test-network-scope.sh uses — against the real
#    vars/homelabinfra-defaults.yml plus the fixtures, so a change to precedence or
#    to combine(recursive=True) discipline is caught without starting Ansible.
# 3. Each tasks/wiring/<provider>.yml gate condition is rendered the same way and
#    checked for the provider-none-is-a-no-op / configured-provider-is-exclusive
#    property docs/specs/provider-noop-wiring.md requires, plus a check that the
#    provider's own assert task degrades (fails, not silently skips) when it lacks
#    the credentials it needs.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
fixtures="$repo/gate/fixtures/config"

fail() { echo "config-fixtures test failed: $*" >&2; exit 1; }

py_bin="$(bash "$repo/ansible/scripts/resolve-python.sh")" || fail "no python resolved"

# ── 1. config-doctor.sh: valid passes, invalid names every problem ────────────
if ! bash "$repo/ansible/scripts/config-doctor.sh" "$fixtures/valid" >/tmp/config-doctor-valid.$$ 2>&1; then
  cat /tmp/config-doctor-valid.$$ >&2
  rm -f /tmp/config-doctor-valid.$$
  fail "the valid fixture must pass config-doctor.sh"
fi
grep -q '^OK\|0 error(s)' /tmp/config-doctor-valid.$$ \
  || fail "valid fixture did not report zero errors"
rm -f /tmp/config-doctor-valid.$$

set +e
invalid_out="$(bash "$repo/ansible/scripts/config-doctor.sh" "$fixtures/invalid" 2>&1)"
invalid_rc=$?
set -e
[ "$invalid_rc" -ne 0 ] || fail "the invalid fixture must fail config-doctor.sh"
for expected in \
  'proxmox.node: required' \
  'proxmox.api_user: required' \
  'proxmox.api_token_id: required' \
  'networks.default.gateway: required' \
  'networks.default.dns_servers: required' \
  'ansible.ssh_public_key: required' \
  'infrastructure.yml.*does not exist' \
  'routing: must be a mapping' \
  'stack: must be a scalar stack name'
do
  grep -qE -- "$expected" <<<"$invalid_out" \
    || fail "invalid fixture did not report expected failure: $expected"
done

# ── 2 & 3. Lift and render the real Jinja from the task files ─────────────────
"$py_bin" - "$repo" "$fixtures" <<'PY'
import re
import sys
from pathlib import Path

import yaml
from jinja2 import ChainableUndefined
from jinja2.nativetypes import NativeEnvironment

repo = Path(sys.argv[1])
fixtures = Path(sys.argv[2])
failures = []


def check(label, got, want):
    if got != want:
        failures.append("%s\n     expected %r\n          got %r" % (label, want, got))


# ChainableUndefined: `homelabinfra_infra.dns.provider` on a dict missing `dns`
# resolves the same way Ansible's own Jinja does -- an Undefined that tolerates
# further attribute/item access -- so `| default('none')` at the end of the chain
# is what actually decides, instead of a mid-chain AttributeError.
env = NativeEnvironment(undefined=ChainableUndefined)


def _combine(base, *others, recursive=False, **_ignored):
    result = dict(base or {})
    for other in others:
        for key, value in dict(other or {}).items():
            if recursive and isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = _combine(result[key], value, recursive=True)
            else:
                result[key] = value
    return result


env.filters["combine"] = _combine


def _default(value, default_=None, boolean=False):
    from jinja2 import Undefined
    if isinstance(value, Undefined) or value is None:
        return default_
    if boolean and not value:
        return default_
    return value


env.filters["default"] = _default

# ── Recursive merge: lift the exact expression from load-user-vars.yml ────────
loader_file = repo / "ansible" / "tasks" / "load-user-vars.yml"
tasks = yaml.safe_load(loader_file.read_text(encoding="utf-8"))
merge_task = next(
    (t for t in tasks
     if t.get("name") == "Merge config layers into homelabinfra_config"),
    None,
)
if merge_task is None:
    raise SystemExit(
        "config-fixtures test: load-user-vars.yml no longer has a "
        "'Merge config layers into homelabinfra_config' task; the merge moved, "
        "update this test to read it where it now lives."
    )
merge_expr = merge_task["ansible.builtin.set_fact"]["homelabinfra_config"]

defaults_raw = yaml.safe_load(
    (repo / "ansible" / "vars" / "homelabinfra-defaults.yml").read_text(encoding="utf-8")
)
homelabinfra_defaults = defaults_raw["homelabinfra_defaults"]

config_proxmox = yaml.safe_load((fixtures / "valid" / "proxmox.yml").read_text(encoding="utf-8"))
config_infrastructure = yaml.safe_load(
    (fixtures / "valid" / "infrastructure.yml").read_text(encoding="utf-8")
)


def merged(pre_existing_config=None):
    return env.from_string(merge_expr).render(
        homelabinfra_defaults=homelabinfra_defaults,
        _config_proxmox=config_proxmox,
        _config_infrastructure=config_infrastructure,
        homelabinfra_config=pre_existing_config or {},
    )


result = merged()

# Property: a key the fixture never mentions still carries the git-managed default —
# a bad merge order (e.g. dropping the defaults layer) loses this silently.
if not homelabinfra_defaults:
    failures.append("vars/homelabinfra-defaults.yml unwrapped to nothing; fixture assumption stale")
else:
    default_only_keys = [
        k for k in homelabinfra_defaults
        if k not in ("proxmox", "networks", "ansible", "infrastructure")
    ]
    for key in default_only_keys:
        check("default-only key %r survives the merge" % key, result.get(key),
              homelabinfra_defaults[key])

# Property: config/proxmox.yml's top-level keys land unwrapped under homelabinfra_config.
check("proxmox.yml -> homelabinfra_config.proxmox.api_host",
      result["proxmox"]["api_host"], config_proxmox["proxmox"]["api_host"])
check("proxmox.yml -> homelabinfra_config.networks.default.cidr",
      result["networks"]["default"]["cidr"], config_proxmox["networks"]["default"]["cidr"])

# Property: a band that declares only ip_offset does not lose the sibling keys already
# present under networks.default from a lower layer — recursive combine, not replace.
check("a partial networks.shared band does not erase a defaulted sibling key",
      result["networks"]["shared"]["ip_offset"], 20)

# Property: config/infrastructure.yml is wrapped whole under .infrastructure.
check("infrastructure.yml -> homelabinfra_config.infrastructure.domain",
      result["infrastructure"]["domain"], config_infrastructure["domain"])
check("infrastructure.yml -> homelabinfra_config.infrastructure.reverse_proxy.provider",
      result["infrastructure"]["reverse_proxy"]["provider"],
      config_infrastructure["reverse_proxy"]["provider"])

# Property: a pre-existing homelabinfra_config (the user_vars_file / back-compat layer)
# wins on conflict but does not erase sibling keys from the lower layers — the whole
# point of combine(recursive=True) over a bare set_fact (namespace-merge-discipline.md).
overridden = merged(pre_existing_config={"infrastructure": {"domain": "override.example.test"}})
check("a higher layer overrides one key",
      overridden["infrastructure"]["domain"], "override.example.test")
check("a higher layer overriding one key keeps its sibling",
      overridden["infrastructure"]["reverse_proxy"]["provider"],
      config_infrastructure["reverse_proxy"]["provider"])

# ── Provider no-op wiring: lift each tasks/wiring/<provider>.yml gate ─────────
PROVIDER_CASES = [
    # (file, role key, provider name)
    ("caddy.yml", "reverse_proxy", "caddy"),
    ("nginx.yml", "reverse_proxy", "nginx"),
    ("authentik.yml", "sso", "authentik"),
    ("pihole.yml", "dns", "pihole"),
    ("opnsense.yml", "dns", "opnsense"),
    ("uptime-kuma.yml", "monitoring", "uptime_kuma"),
]


def render_when(expr, homelabinfra_infra):
    rendered = env.from_string("{{ (%s) }}" % expr).render(
        homelabinfra_infra=homelabinfra_infra
    )
    return str(rendered).strip().lower() == "true"


def gate_condition(task):
    when = task.get("when")
    if when is None:
        return None
    if isinstance(when, list):
        return " and ".join("(%s)" % w for w in when)
    return str(when)


for filename, role_key, provider in PROVIDER_CASES:
    path = repo / "ansible" / "tasks" / "wiring" / filename
    wiring_tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    gated = [t for t in wiring_tasks if t.get("when") is not None]
    if not gated:
        failures.append("%s: no gated task found — cannot confirm the no-op contract"
                         % filename)
        continue
    task = gated[0]
    expr = gate_condition(task)

    # Absent role key entirely: must not run (provider is a no-op silently, per spec).
    try:
        ran = render_when(expr, {})
    except Exception as exc:  # noqa: BLE001 - report as a failure, not a crash
        failures.append("%s: could not render its when: condition (%s)" % (filename, exc))
        continue
    check("%s is a no-op when homelabinfra_infra has no %r key" % (filename, role_key),
          ran, False)

    # Explicit provider: none: must not run.
    ran = render_when(expr, {role_key: {"provider": "none"}})
    check("%s is a no-op when %s.provider is none" % (filename, role_key), ran, False)

    # A different configured provider on the same role key: must not run either —
    # provider selection is exclusive, one role key drives exactly one wiring file.
    ran = render_when(expr, {role_key: {"provider": "not-" + provider}})
    check("%s does not fire for a different provider on %s" % (filename, role_key),
          ran, False)

    # Its own provider: must run.
    ran = render_when(expr, {role_key: {"provider": provider}})
    check("%s fires when %s.provider is %r" % (filename, role_key, provider), ran, True)

# ── Configured-provider failure path: no credentials degrades, does not silently
#    skip. The seam is the assert task inside the same gated block — the wiring
#    file only reaches it once the provider actually matches, and every
#    tasks/wiring/<provider>.yml constructed above has one.
for filename, role_key, provider in PROVIDER_CASES:
    path = repo / "ansible" / "tasks" / "wiring" / filename
    text = path.read_text(encoding="utf-8")
    if "ansible.builtin.assert" not in text:
        failures.append(
            "%s: no assert task guards a configured-but-uncredentialed %s.%s -- "
            "a missing credential would silently no-op instead of failing"
            % (filename, role_key, provider)
        )

if failures:
    print("config-fixtures: %d failure(s)" % len(failures), file=sys.stderr)
    for f in failures:
        print("  FAIL %s" % f, file=sys.stderr)
    raise SystemExit(1)
print("config-fixtures: OK")
PY

echo "Config fixture tests passed."
