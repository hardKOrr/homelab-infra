#!/usr/bin/env bash
# The scope -> network decision, evaluated against the real expressions.
#
# ansible/tasks/network/resolve-network.yml is what decides which named network — and so
# which VLAN — a guest is addressed on. Getting that wrong is quiet: a mistake resolves to
# `default`, the guest comes up, wiring succeeds, and the estate isolation the operator
# declared simply is not there. --syntax-check cannot see that, and neither can a lab that
# has not been segmented yet, which is every lab until the VLANs are cut.
#
# So the Jinja is lifted out of the task file and rendered here rather than restated: a
# change to those expressions is what these cases are compared against.
#
# The two properties that matter most, and are the easiest to break:
#   1. A lab with one flat `default` network resolves to `default` in EVERY case. The scope
#      steps are advisory, and a lab that has declared nothing must be untouched.
#   2. An estate-scoped guest never resolves to another estate's network, and never to
#      `shared` — that name belongs to `scope: lab` and to `shared: true` stacks alone.
#
# No Ansible is started and no Proxmox is contacted: pure Python against the repository.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

python3 - "$repo" <<'PY'
import sys
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

repo = Path(sys.argv[1])
failures = []

task_file = repo / "ansible" / "tasks" / "network" / "resolve-network.yml"
tasks = yaml.safe_load(task_file.read_text(encoding="utf-8"))
SET_FACT = ("ansible.builtin.set_fact", "set_fact")


def set_fact_of(task):
    for key in SET_FACT:
        if key in task:
            return task[key]
    return {}


decide = next(
    (t for t in tasks if "network_selected" in set_fact_of(t)), None
)
if decide is None:
    raise SystemExit(
        "network-scope test: no set_fact publishing network_selected in "
        "resolve-network.yml. The decision moved; update this test to read it "
        "where it now lives."
    )

env = NativeEnvironment()
# The Ansible filters these expressions use. Shimmed, not imported: ansible-core need not be
# importable for the gate to check the arithmetic.
env.filters["dict2items"] = lambda mapping: [
    {"key": k, "value": v} for k, v in dict(mapping).items()
]
env.filters["bool"] = lambda value: (
    value if isinstance(value, bool)
    else str(value).strip().lower() in ("true", "yes", "on", "1")
)

# Ansible resolves `vars:` lazily and in dependency order; here that order is written out.
ORDER = ["_entry", "_domains", "_default_estate", "_estate", "_estate_network",
         "_shared", "_requested", "_hint"]


def resolve(catalog, config, app="", estate="", shared=None, requested=""):
    ctx = {
        "_resolve_network_catalog": catalog,
        "homelabinfra_config": config,
        "resolve_network_app": app,
        "resolve_network_estate": estate,
        "resolve_network_requested": requested,
    }
    if shared is not None:
        ctx["resolve_network_shared"] = shared
    declared = decide["vars"]
    for name in ORDER:
        if name in declared:
            ctx[name] = env.from_string(str(declared[name])).render(**ctx)
    return {
        key: env.from_string(str(expr)).render(**ctx)
        for key, expr in set_fact_of(decide).items()
    }


def check(label, got, want):
    if got != want:
        failures.append("%s\n     expected %r\n          got %r" % (label, want, got))


# The catalog's real classification is what the resolver reads, so the real file is used.
catalog = yaml.safe_load((repo / "catalog" / "applications.yml").read_text(encoding="utf-8"))
apps = catalog["applications"]
for slug, scope in (("caddy", "lab"), ("vaultwarden", "lab"), ("pbs", "lab"),
                    ("sonarr", "estate"), ("authentik", "estate")):
    if apps.get(slug, {}).get("scope") != scope:
        failures.append(
            "catalog/applications.yml: %s is no longer scope: %s, which these cases assume"
            % (slug, scope)
        )

FLAT = {"networks": {"default": {}}, "infrastructure": {}}
SEGMENTED = {
    "networks": {"default": {}, "shared": {}, "personal": {}, "foxglove": {}, "iot": {}},
    "infrastructure": {"domains": {
        "personal": {"domain": "a.example", "default": True},
        "foxglove": {"domain": "b.example"},
    }},
}
NAMED_ESTATE_NET = {
    "networks": {"default": {}, "shared": {}, "vlan21": {}},
    "infrastructure": {"domains": {
        "personal": {"domain": "a.example", "default": True},
        "foxglove": {"domain": "b.example", "network": "vlan21"},
    }},
}

# -- Property 1: a lab that declared nothing is untouched ----------------------
check("flat lab, lab-scoped app",
      resolve(catalog, FLAT, app="caddy")["network_selected"], "default")
check("flat lab, estate app",
      resolve(catalog, FLAT, app="sonarr")["network_selected"], "default")
check("flat lab, shared stack",
      resolve(catalog, FLAT, shared=True)["network_selected"], "default")
check("flat lab, no domains map, no app",
      resolve(catalog, FLAT)["network_selected"], "default")

# -- Property 2: the estate boundary holds ------------------------------------
check("lab-scoped app takes shared",
      resolve(catalog, SEGMENTED, app="caddy")["network_selected"], "shared")
check("the vault takes shared",
      resolve(catalog, SEGMENTED, app="vaultwarden")["network_selected"], "shared")
check("estate app, named estate",
      resolve(catalog, SEGMENTED, app="sonarr", estate="foxglove")["network_selected"],
      "foxglove")
check("estate app, unnamed estate takes the DEFAULT estate, not shared",
      resolve(catalog, SEGMENTED, app="sonarr")["network_selected"], "personal")
check("an estate's own SSO stays in its estate",
      resolve(catalog, SEGMENTED, app="authentik", estate="foxglove")["network_selected"],
      "foxglove")

# -- Stacks: not applications, so shared is declared rather than looked up -----
check("shared stack takes shared",
      resolve(catalog, SEGMENTED, estate="foxglove", shared=True)["network_selected"],
      "shared")
check("ordinary stack stays in its estate",
      resolve(catalog, SEGMENTED, estate="foxglove", shared=False)["network_selected"],
      "foxglove")
check("shared passed as an Ansible string, not a bool",
      resolve(catalog, SEGMENTED, estate="foxglove", shared="True")["network_selected"],
      "shared")

# -- An explicit name always wins ---------------------------------------------
check("explicit network beats scope",
      resolve(catalog, SEGMENTED, app="caddy", requested="iot")["network_selected"], "iot")
check("explicit network beats an estate",
      resolve(catalog, SEGMENTED, app="sonarr", estate="foxglove",
              requested="iot")["network_selected"], "iot")

# -- An estate may name a network that is not called after it -----------------
check("estate names its own network",
      resolve(catalog, NAMED_ESTATE_NET, app="sonarr", estate="foxglove")["network_selected"],
      "vlan21")
check("an estate declaring no network, and none named after it, falls back",
      resolve(catalog, NAMED_ESTATE_NET, app="sonarr", estate="personal")["network_selected"],
      "default")

# -- The hint is published even where it is not used --------------------------
# generate-ip.yml re-applies the same advisory rule from `network_hint`, so the two have to
# agree about what the scope WAS, independently of what the lab happens to declare.
check("hint names the estate even in a flat lab",
      resolve(catalog, FLAT, app="sonarr", estate="foxglove")["network_hint"], "foxglove")
check("hint names shared even in a flat lab",
      resolve(catalog, FLAT, app="caddy")["network_hint"], "shared")

if failures:
    print("network-scope: %d failure(s)" % len(failures), file=sys.stderr)
    for f in failures:
        print("  FAIL %s" % f, file=sys.stderr)
    raise SystemExit(1)
print("network-scope: OK (%d cases)" % 18)
PY
