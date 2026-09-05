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
import os
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


def _lookup(plugin, key, default=None, true=False):
    if plugin != "env":
        raise ValueError("only the env lookup is shimmed for this gate test: %r" % plugin)
    return os.environ.get(key, "" if default is None else default)


env.filters["combine"] = _combine
env.filters["dict2items"] = lambda mapping: [
    {"key": k, "value": v} for k, v in dict(mapping).items()
]
env.filters["bool"] = lambda value: (
    value if isinstance(value, bool)
    else str(value).strip().lower() in ("true", "yes", "on", "1")
)
env.globals["lookup"] = _lookup


def render(expr, **ctx):
    return env.from_string(str(expr)).render(**ctx)


SET_FACT = ("ansible.builtin.set_fact", "set_fact")
ASSERT = ("ansible.builtin.assert", "assert")
INCLUDE_TASKS = ("ansible.builtin.include_tasks", "include_tasks")


def load_tasks(relpath):
    return yaml.safe_load((repo / relpath).read_text(encoding="utf-8"))


def find_task(tasks, name):
    for task in tasks:
        if task.get("name") == name:
            return task
    raise SystemExit(
        "%s: no task named %r -- it moved or was renamed; update this test "
        "to read it where it now lives" % (name, name)
    )


def find_play_task(plays, play_name, task_name):
    for play in plays:
        if play.get("name") == play_name:
            return find_task(play["tasks"], task_name)
    raise SystemExit("no play named %r" % play_name)


def module_body(task, module_names):
    for key in module_names:
        if key in task:
            return task[key]
    raise SystemExit("task %r declares none of %s" % (task.get("name"), module_names))


def render_vars_in_order(vars_map, ctx):
    """Ansible resolves `vars:` lazily in declaration order; iterate and accumulate,
    exactly as gate/test-network-scope.sh does for resolve-network.yml."""
    rendered = dict(ctx)
    for name, expr in vars_map.items():
        rendered[name] = env.from_string(str(expr)).render(**rendered)
    return rendered


# -- Part 1: the REAL combine(recursive=True) precedence chain ---------------
# Read straight out of ansible/tasks/load-user-vars.yml's
# "Merge config layers into homelabinfra_config" task, so removing recursive=True
# or reordering the chain there fails this test without anyone touching it here.
luv_tasks = load_tasks("ansible/tasks/load-user-vars.yml")
merge_task = find_task(luv_tasks, "Merge config layers into homelabinfra_config")
merge_expr = module_body(merge_task, SET_FACT)["homelabinfra_config"]

# storage/schedule are deliberately absent from the config_proxmox/config_infrastructure
# layers, so a merge that replaces `proxmox`/`infrastructure` WHOLE instead of per-key
# (i.e. recursive=True dropped from the real task) loses them and this test catches it --
# a config_proxmox that fully overwrote every default key would pass either way.
defaults = {"infrastructure": {"backups": {"schedule": "daily"}},
            "proxmox": {"api_port": 8006, "storage": "local-lvm"}}
config_proxmox = {"proxmox": {"node": "pve", "api_port": 8007}}
config_infrastructure = {"domain": "lab.example", "backups": {"datastore_path": "/mnt/backup"}}
user_vars_override = {"proxmox": {"node": "pve-override"}}

result = render(merge_expr,
                 homelabinfra_defaults=defaults,
                 _config_proxmox=config_proxmox,
                 _config_infrastructure=config_infrastructure,
                 homelabinfra_config={})
check("proxmox.yml overrides only api_port, node is unset by defaults",
      result["proxmox"]["node"], "pve")
check("proxmox.yml's api_port wins over the defaults seed",
      result["proxmox"]["api_port"], 8007)
check("a per-key recursive merge keeps the defaults' proxmox.storage even though "
      "config_proxmox's proxmox mapping does not repeat it",
      result["proxmox"]["storage"], "local-lvm")
check("infrastructure.yml's domain lands under .infrastructure",
      result["infrastructure"]["domain"], "lab.example")
check("recursive merge keeps the defaults' backups.schedule alongside "
      "infrastructure.yml's backups.datastore_path",
      result["infrastructure"]["backups"],
      {"schedule": "daily", "datastore_path": "/mnt/backup"})

# user_vars_file / env overlays are the pre-existing homelabinfra_config and win last.
result_override = render(merge_expr,
                          homelabinfra_defaults=defaults,
                          _config_proxmox=config_proxmox,
                          _config_infrastructure=config_infrastructure,
                          homelabinfra_config=user_vars_override)
check("the highest-precedence layer (user_vars_file/env) wins over config/proxmox.yml",
      result_override["proxmox"]["node"], "pve-override")

# Absent config/*.yml (a clean checkout) must not crash the merge -- both includes use
# failed_when: false and default to {} in the merge, per load-user-vars.yml's own
# header comment.
result_absent = render(merge_expr, homelabinfra_defaults=defaults, homelabinfra_config={})
check("an absent config/proxmox.yml and config/infrastructure.yml still resolve, "
      "seeded only from defaults",
      result_absent["proxmox"], {"api_port": 8006, "storage": "local-lvm"})

# -- Part 1b: the REAL environment-secret overlay, precedence and absence ----
# "Overlay secrets supplied through the environment" -- environment wins over the file
# value when set, and an absent environment must leave homelabinfra_config untouched.
env_task = find_task(luv_tasks, "Overlay secrets supplied through the environment")
env_expr = module_body(env_task, SET_FACT)["homelabinfra_config"]
env_vars = env_task["vars"]

file_shaped_config = {"proxmox": {"api_token_secret": "from-file-not-a-real-secret"}}

os.environ.pop("PROXMOX_API_TOKEN", None)
os.environ.pop("PROXMOX_API_TOKEN_ID", None)
os.environ.pop("PROXMOX_API_USER", None)
os.environ.pop("VAULTWARDEN_ADMIN_TOKEN", None)
os.environ.pop("CLOUDFLARE_API_TOKEN", None)

ctx_absent = render_vars_in_order(env_vars, {"homelabinfra_config": file_shaped_config})
result_env_absent = render(env_expr, **ctx_absent)
check("an absent PROXMOX_API_TOKEN leaves the file-supplied secret untouched",
      result_env_absent["proxmox"]["api_token_secret"], "from-file-not-a-real-secret")

os.environ["PROXMOX_API_TOKEN"] = "from-env-not-a-real-secret"
try:
    ctx_present = render_vars_in_order(env_vars, {"homelabinfra_config": file_shaped_config})
    result_env_present = render(env_expr, **ctx_present)
finally:
    del os.environ["PROXMOX_API_TOKEN"]
check("PROXMOX_API_TOKEN from the environment overlays LAST and wins over the file value",
      result_env_present["proxmox"]["api_token_secret"], "from-env-not-a-real-secret")

# -- Part 2: estate overlay is a REAL WHOLE-KEY replace, never recursive -----
# Read straight out of ansible/tasks/resolve-estate.yml's "Overlay estate-scoped
# facts" task: domain/sso/dns are replaced whole so a default-estate credential
# can never leak into another estate's wiring. Making this merge recursive, or
# changing the {'dns': ...} conditional, fails this test without editing it.
estate_tasks = load_tasks("ansible/tasks/resolve-estate.yml")
overlay_task = find_task(estate_tasks, "Resolve estate | Overlay estate-scoped facts")
overlay_expr = module_body(overlay_task, SET_FACT)["homelabinfra_infra"]
overlay_vars = overlay_task["vars"]
overlay_when = overlay_task["when"]

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


def overlay_for(infra, selected, default):
    base_ctx = {
        "homelabinfra_infra": infra,
        "_estate_domains": estate_domains,
        "_estate_selected": selected,
        "_estate_default": default,
    }
    ctx = render_vars_in_order(overlay_vars, base_ctx)
    fires = all(render("{{ %s }}" % cond, **ctx) for cond in overlay_when)
    result = render(overlay_expr, **ctx) if fires else infra
    return result, fires


overlaid, fired = overlay_for(homelabinfra_infra, "foxglove", "personal")
check("the overlay's own when: conditions fire for a genuine non-default estate",
      fired, True)
check("a non-default estate takes its own domain", overlaid["domain"], "foxglove.fixture.invalid")
check("a non-default estate's sso is replaced WHOLE, not merged",
      overlaid["sso"], {"provider": "none"})
check("the default estate's sso admin_password does not leak into another estate's sso",
      "admin_password" in overlaid["sso"], False)
check("an estate that declares no dns of its own inherits the global dns entry whole",
      overlaid["dns"], homelabinfra_infra["dns"])

_, fired_default = overlay_for(homelabinfra_infra, "personal", "personal")
check("the overlay is a real no-op (when: false) for the default estate itself",
      fired_default, False)

# An estate WITH its own authored dns block replaces it whole too (non-secret half only
# -- the credential is layered on separately by "Overlay the estate's authored DNS
# provider", not exercised here).
homelabinfra_infra_with_estate_dns = dict(homelabinfra_infra)
homelabinfra_infra_with_estate_dns["estates"] = {
    "foxglove": {"sso": {"provider": "none"},
                 "dns": {"provider": "pihole", "host": "198.51.100.9"}},
}
overlaid_dns, _ = overlay_for(homelabinfra_infra_with_estate_dns, "foxglove", "personal")
check("an estate with its own dns block replaces the global one whole",
      overlaid_dns["dns"], {"provider": "pihole", "host": "198.51.100.9"})

# -- Part 3: the REAL "exactly one default: true" assert condition -----------
# Read straight out of resolve-estate.yml's "Compute estate names" set_fact and the
# following "Require one explicit multi-estate default" assert -- the actual
# `_estate_defaults` computation and the actual `that:` expression, not a
# len()-based re-implementation.
compute_task = find_task(estate_tasks, "Resolve estate | Compute estate names")
compute_fields = module_body(compute_task, SET_FACT)
assert_task = find_task(estate_tasks, "Resolve estate | Require one explicit multi-estate default")
assert_that = module_body(assert_task, ASSERT)["that"]


def assert_holds(domains):
    ctx = {"_estate_infra_cfg": {"domains": domains}}
    ctx["_estate_domains"] = render(compute_fields["_estate_domains"], **ctx)
    ctx["_estate_defaults"] = render(compute_fields["_estate_defaults"], **ctx)
    conditions = assert_that if isinstance(assert_that, list) else [assert_that]
    return all(render("{{ %s }}" % cond, **ctx) for cond in conditions)


check("two estates, one default: true -- the real assert condition holds",
      assert_holds(estate_domains), True)

no_default = {
    "personal": {"domain": "personal.fixture.invalid"},
    "foxglove": {"domain": "foxglove.fixture.invalid"},
}
check("two estates, neither default: true -- the real assert condition fails "
      "(declaration order must not decide)",
      assert_holds(no_default), False)

two_defaults = {
    "personal": {"domain": "personal.fixture.invalid", "default": True},
    "foxglove": {"domain": "foxglove.fixture.invalid", "default": True},
}
check("two estates, both default: true -- the real assert condition fails",
      assert_holds(two_defaults), False)

# -- Part 4: the REAL provider-none no-op contract ----------------------------
# Read straight out of ansible/playbooks/apps/caddy.yml's "Wire DNS" task --
# the actual `when:` list every app playbook repeats for its own provider role
# key, not a hand-written stand-in. docs/specs/provider-noop-wiring.md is the
# normative spec this task implements.
caddy_plays = load_tasks("ansible/playbooks/apps/caddy.yml")
wire_dns_task = find_play_task(caddy_plays, "Caddy | Record facts and wire", "Wire DNS")
wire_dns_when = wire_dns_task["when"]


UNDEFINED = object()


def dns_wiring_fires(homelabinfra_infra_value):
    ctx = {} if homelabinfra_infra_value is UNDEFINED \
        else {"homelabinfra_infra": homelabinfra_infra_value}
    return all(render("{{ %s }}" % cond, **ctx) for cond in wire_dns_when)


check("homelabinfra_infra undefined entirely (pre-bootstrap, per "
      "docs/specs/provider-noop-wiring.md) is a silent no-op",
      dns_wiring_fires(UNDEFINED), False)
check("homelabinfra_infra defined but .dns absent is a silent no-op",
      dns_wiring_fires({}), False)
check("an explicit dns.provider: none is a silent no-op",
      dns_wiring_fires({"dns": {"provider": "none"}}), False)
check("a configured dns.provider fires the wiring gate",
      dns_wiring_fires({"dns": {"provider": "opnsense"}}), True)

if failures:
    print("config-loading: %d failure(s)" % len(failures), file=sys.stderr)
    for f in failures:
        print("  FAIL %s" % f, file=sys.stderr)
    raise SystemExit(1)
print("config-loading focused tests (python half) passed.")
PY
