#!/usr/bin/env bash
# Focused regression tests for the config-loading seam (issue #30, child of #29):
#   - the combine(recursive=True) precedence chain ansible/tasks/load-user-vars.yml builds
#     per ansible/vars/CONTRACT.md §4
#   - estate/network overlay behavior in ansible/tasks/resolve-estate.yml (whole-key
#     replace, never a recursive merge, so a default-estate token cannot leak into
#     another estate)
#   - the provider-none no-op contract (docs/specs/provider-noop-wiring.md)
#   - generated-fact secret rejection (ansible/scripts/secret-shape.py)
#   - config-doctor.sh against tracked, synthetic fixtures
#
# Every fixture lives under gate/fixtures/config-loading/ and is synthetic: no
# credential, token, private key, password or live/routable endpoint. Nothing here
# writes to config/ (gitignored, user-owned live-lab state) or contacts Proxmox, and
# nothing runs a mutating application deploy. Where the real task file resolves its
# config directory relative to the repo tree (load-user-vars.yml, resolve-estate.yml),
# this test re-renders the task's own Jinja expressions against synthetic in-memory
# vars instead of pointing those tasks at a fixture directory, exactly as
# gate/test-network-scope.sh does for resolve-network.yml. config-doctor.sh alone takes
# its config directory as an argument, so the fixtures ARE exercised against the real
# script.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
fixtures="$repo/gate/fixtures/config-loading"

fail() { echo "config-loading test failed: $*" >&2; exit 1; }

# ── config-doctor.sh against the tracked fixtures ─────────────────────────────
doctor() {
  local dir="$1"
  bash "$repo/ansible/scripts/config-doctor.sh" "$dir"
}

out="$(doctor "$fixtures/valid")" \
  || fail "valid fixture was rejected (exit != 0 means an ERROR was reported): $out"
! grep -q "^ERROR" <<<"$out" \
  || fail "valid fixture reported an ERROR: $out"

out="$(doctor "$fixtures/multi-estate")" \
  || fail "multi-estate fixture was rejected (exit != 0 means an ERROR was reported): $out"
! grep -q "^ERROR" <<<"$out" \
  || fail "multi-estate fixture reported an ERROR: $out"

set +e
out="$(doctor "$fixtures/invalid" 2>&1)"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "invalid fixture (missing proxmox.node, no default gateway) was accepted"
grep -q "proxmox.node" <<<"$out" \
  || fail "invalid-fixture report did not name the missing proxmox.node key: $out"
grep -q "networks.default.gateway" <<<"$out" \
  || fail "invalid-fixture report did not name the missing gateway key: $out"

# ── secret-shape.py: generated-fact secret rejection ──────────────────────────
shape="$repo/ansible/scripts/secret-shape.py"

clean='{"domain":"fixture.invalid","reverse_proxy":{"provider":"caddy","host":"http://198.51.100.5"}}'
printf '%s' "$clean" | python3 "$shape" >/dev/null \
  || fail "secret-shape.py rejected a clean generated-facts document"

dirty='{"domain":"fixture.invalid","reverse_proxy":{"provider":"nginx","admin_password":"x"}}'
set +e
err="$(printf '%s' "$dirty" | python3 "$shape" 2>&1 >/dev/null)"
rc=$?
set -e
[ "$rc" -eq 2 ] || fail "secret-shape.py did not reject admin_password with exit 2 (got $rc)"
grep -q "admin_password" <<<"$err" \
  || fail "secret-shape.py's refusal did not name the offending field: $err"

# api_token_id is a documented SAFE exception (identifies a token, is not one).
safe='{"backups":{"api_token_id":"abc123"}}'
printf '%s' "$safe" | python3 "$shape" >/dev/null \
  || fail "secret-shape.py rejected the documented api_token_id SAFE exception"

echo "config-loading focused tests (bash half) passed."

python3 - "$repo" <<'PY'
import sys
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

repo = Path(sys.argv[1])
failures = []


def check(label, got, want):
    if got != want:
        failures.append("%s\n     expected %r\n          got %r" % (label, want, got))


env = NativeEnvironment()


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
env.filters["dict2items"] = lambda mapping: [
    {"key": k, "value": v} for k, v in dict(mapping).items()
]


def render(expr, **ctx):
    return env.from_string(str(expr)).render(**ctx)


# -- Part 1: the combine(recursive=True) precedence chain --------------------
# Mirrors the merge in ansible/tasks/load-user-vars.yml (repo lines ~96-102):
#   homelabinfra_defaults -> config/proxmox.yml -> {'infrastructure': config/infrastructure.yml}
#   -> pre-existing homelabinfra_config (user_vars_file / env overlays)
# combine(recursive=True), later layers win PER KEY, not whole-mapping.
EXPR = (
    "{{ homelabinfra_defaults | default({})"
    " | combine(_config_proxmox | default({}), recursive=True)"
    " | combine({'infrastructure': _config_infrastructure | default({})}, recursive=True)"
    " | combine(homelabinfra_config | default({}), recursive=True) }}"
)

defaults = {"infrastructure": {"backups": {"schedule": "daily"}}, "proxmox": {"api_port": 8006}}
config_proxmox = {"proxmox": {"node": "pve", "api_port": 8007}}
config_infrastructure = {"domain": "lab.example", "backups": {"datastore_path": "/mnt/backup"}}
user_vars_override = {"proxmox": {"node": "pve-override"}}

result = render(EXPR,
                 homelabinfra_defaults=defaults,
                 _config_proxmox=config_proxmox,
                 _config_infrastructure=config_infrastructure,
                 homelabinfra_config={})
check("proxmox.yml overrides only api_port, node is unset by defaults",
      result["proxmox"]["node"], "pve")
check("proxmox.yml's api_port wins over the defaults seed",
      result["proxmox"]["api_port"], 8007)
check("infrastructure.yml's domain lands under .infrastructure",
      result["infrastructure"]["domain"], "lab.example")
check("recursive merge keeps the defaults' backups.schedule alongside "
      "infrastructure.yml's backups.datastore_path",
      result["infrastructure"]["backups"],
      {"schedule": "daily", "datastore_path": "/mnt/backup"})

# user_vars_file / env overlays are the pre-existing homelabinfra_config and win last.
result_override = render(EXPR,
                          homelabinfra_defaults=defaults,
                          _config_proxmox=config_proxmox,
                          _config_infrastructure=config_infrastructure,
                          homelabinfra_config=user_vars_override)
check("the highest-precedence layer (user_vars_file/env) wins over config/proxmox.yml",
      result_override["proxmox"]["node"], "pve-override")

# Absent config/*.yml (a clean checkout) must not crash the merge -- both includes use
# failed_when: false and default to {} in the merge, per load-user-vars.yml's own
# header comment.
result_absent = render(EXPR, homelabinfra_defaults=defaults, homelabinfra_config={})
check("an absent config/proxmox.yml and config/infrastructure.yml still resolve, "
      "seeded only from defaults",
      result_absent["proxmox"], {"api_port": 8006})

# -- Part 2: estate overlay is a WHOLE-KEY replace, never recursive ----------
# Mirrors ansible/tasks/resolve-estate.yml's "Overlay estate-scoped facts" task
# (repo lines ~103-118): domain/sso/dns are replaced whole so a default-estate
# credential can never leak into another estate's wiring.
ESTATE_OVERLAY_EXPR = (
    "{{ homelabinfra_infra | combine("
    "{'domain': _estate_domains[_estate_selected].domain,"
    " 'sso': _estate_scope.sso | default({'provider': 'none'})}"
    " | combine({'dns': _estate_scope.dns} if _estate_scope.dns is defined else {})"
    ") }}"
)

homelabinfra_infra = {
    "domain": "personal.fixture.invalid",
    "sso": {"provider": "authentik", "instance": "authentik",
            "admin_user": "akadmin", "admin_password": "personal-secret"},
    "dns": {"provider": "opnsense", "host": "198.51.100.1", "api_secret": "personal-dns-secret"},
    "estates": {
        "foxglove": {"sso": {"provider": "none"}},
    },
}
estate_domains = {
    "personal": {"domain": "personal.fixture.invalid", "default": True},
    "foxglove": {"domain": "foxglove.fixture.invalid", "network": "foxglove"},
}

overlaid = render(
    ESTATE_OVERLAY_EXPR,
    homelabinfra_infra=homelabinfra_infra,
    _estate_domains=estate_domains,
    _estate_selected="foxglove",
    _estate_scope=homelabinfra_infra["estates"]["foxglove"],
)
check("a non-default estate takes its own domain", overlaid["domain"], "foxglove.fixture.invalid")
check("a non-default estate's sso is replaced WHOLE, not merged",
      overlaid["sso"], {"provider": "none"})
check("the default estate's sso admin_password does not leak into another estate's sso",
      "admin_password" in overlaid["sso"], False)
check("an estate that declares no dns of its own inherits the global dns entry whole",
      overlaid["dns"], homelabinfra_infra["dns"])

# An estate WITH its own authored dns block replaces it whole too (non-secret half only
# -- the credential is layered on separately by "Overlay the estate's authored DNS
# provider", not exercised here).
homelabinfra_infra_with_estate_dns = dict(homelabinfra_infra)
homelabinfra_infra_with_estate_dns["estates"] = {
    "foxglove": {"sso": {"provider": "none"},
                 "dns": {"provider": "pihole", "host": "198.51.100.9"}},
}
overlaid_dns = render(
    ESTATE_OVERLAY_EXPR,
    homelabinfra_infra=homelabinfra_infra_with_estate_dns,
    _estate_domains=estate_domains,
    _estate_selected="foxglove",
    _estate_scope=homelabinfra_infra_with_estate_dns["estates"]["foxglove"],
)
check("an estate with its own dns block replaces the global one whole",
      overlaid_dns["dns"], {"provider": "pihole", "host": "198.51.100.9"})

# -- Part 3: "exactly one default: true" assert condition --------------------
# Mirrors resolve-estate.yml's "Require one explicit multi-estate default" assert
# (`_estate_defaults | length == 1`, fail_msg: declaration order does not decide).
def defaults_of(domains):
    return [name for name, estate in domains.items() if estate.get("default")]


check("two estates, one default: true -- assert condition holds",
      len(defaults_of(estate_domains)) == 1, True)

no_default = {
    "personal": {"domain": "personal.fixture.invalid"},
    "foxglove": {"domain": "foxglove.fixture.invalid"},
}
check("two estates, neither default: true -- assert condition fails "
      "(declaration order must not decide)",
      len(defaults_of(no_default)) == 1, False)

two_defaults = {
    "personal": {"domain": "personal.fixture.invalid", "default": True},
    "foxglove": {"domain": "foxglove.fixture.invalid", "default": True},
}
check("two estates, both default: true -- assert condition fails",
      len(defaults_of(two_defaults)) == 1, False)

# -- Part 4: the provider-none no-op contract ---------------------------------
# Mirrors the gate every app playbook uses (e.g. ansible/playbooks/apps/caddy.yml
# "reverse_proxy.provider | default('none') != 'none'") and
# docs/specs/provider-noop-wiring.md: absent, explicit 'none', and any other value.
GATE_EXPR = "{{ (registry_entry.provider | default('none')) != 'none' }}"

check("no provider key at all is a silent no-op",
      render(GATE_EXPR, registry_entry={}), False)
check("an explicit provider: none is a silent no-op",
      render(GATE_EXPR, registry_entry={"provider": "none"}), False)
check("a configured provider fires the wiring gate",
      render(GATE_EXPR, registry_entry={"provider": "opnsense"}), True)

if failures:
    print("config-loading: %d failure(s)" % len(failures), file=sys.stderr)
    for f in failures:
        print("  FAIL %s" % f, file=sys.stderr)
    raise SystemExit(1)
print("config-loading focused tests (python half) passed.")
PY
