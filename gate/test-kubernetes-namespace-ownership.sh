#!/usr/bin/env bash
# Focused tests for the Kubernetes namespace-ownership contract issue #34's third
# acceptance criterion needs for the Kind lane: cleanup (ansible/tasks/unwiring/
# kubernetes.yml, exercised by gate/kind-app/teardown.yml) must select only a namespace
# this platform created, and a negative fixture must prove an unowned or unlabelled
# namespace is refused rather than deleted.
#
# No cluster is created and no Ansible play runs: the exact Jinja expressions are pulled
# out of ansible/tasks/kubernetes/derive-namespace.yml and ansible/tasks/unwiring/
# kubernetes.yml and evaluated directly, the same approach gate/test-proxmox-tags.sh uses
# for the Proxmox tag grammar. A copy of the expression here would drift from the real
# task file silently; loading the real file cannot.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

python3 - "$repo" <<'PY'
import re
import sys
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

repo = Path(sys.argv[1])
failures = []
env = NativeEnvironment()
# Ansible ships regex_replace and the `match` test as part of ansible-core's Jinja
# environment, not vanilla jinja2 — registered here the same way
# gate/test-proxmox-tags.sh registers regex_replace, so the real task expressions can be
# evaluated without pulling in all of ansible-core's plugin loader.
env.filters["regex_replace"] = lambda value, pattern, replacement="": re.sub(
    pattern, replacement, value
)
env.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None


def check(label, got, want):
    if got != want:
        failures.append("%s\n     expected %r\n          got %r" % (label, want, got))


# -- derive-namespace.yml: instance -> namespace, and the legality gate on the result ----
derive = yaml.safe_load((repo / "ansible/tasks/kubernetes/derive-namespace.yml").read_text())
namespace_expr = next(
    t["ansible.builtin.set_fact"]["k8s_namespace"]
    for t in derive
    if "ansible.builtin.set_fact" in t
)
legal_expr = next(
    t["ansible.builtin.assert"]["that"][0]
    for t in derive
    if "ansible.builtin.assert" in t and "k8s_namespace_for" not in t["ansible.builtin.assert"]["that"][0]
)


def derive_namespace(instance):
    # namespace_expr is already a full template string ("app-{{ ... }}"), not a bare
    # expression, so it is rendered as-is rather than re-wrapped in another {{ }}.
    return env.from_string(namespace_expr).render(k8s_namespace_for=instance)


def is_legal(namespace):
    return bool(env.from_string("{{ " + legal_expr + " }}").render(k8s_namespace=namespace))


check("namespace for flaresolverr-kind-smoke", derive_namespace("flaresolverr-kind-smoke"),
      "app-flaresolverr-kind-smoke")
check("namespace lowercases and sanitises", derive_namespace("Sonarr_Anime"), "app-sonarr-anime")
check("legal: real instance", is_legal(derive_namespace("flaresolverr-kind-smoke")), True)

# Negative fixture: an instance name that reduces to nothing (all-illegal characters)
# must fail the legality gate, not silently produce a bare "app-" namespace that could
# collide with some other instance's own reduction.
empty_reduction = derive_namespace("___")
check("all-illegal instance reduces to bare 'app-'", empty_reduction, "app-")
check("bare 'app-' is refused as illegal", is_legal(empty_reduction), False)

# -- unwiring/kubernetes.yml: the ownership refusal that gates namespace deletion --------
unwire = yaml.safe_load((repo / "ansible/tasks/unwiring/kubernetes.yml").read_text())
block_task = next(t for t in unwire if "block" in t)
owner_condition = block_task["block"][0]["ansible.builtin.assert"]["that"]


def owned_by_platform(label):
    stdout = type("R", (), {"stdout": label})()
    return bool(env.from_string("{{ " + owner_condition + " }}").render(_k8s_rm_owner=stdout))


check("owned namespace passes the refusal assert", owned_by_platform("homelab-infra"), True)
check("owned namespace with trailing newline (real kubectl -o jsonpath output) still passes",
      owned_by_platform("homelab-infra\n"), True)

# Negative fixtures: cleanup must refuse every one of these rather than deleting the
# namespace, proving an unowned resource is never selected.
for negative_label in ("", "kube-system", "some-other-platform", "Homelab-Infra"):
    check(
        f"unowned/foreign namespace label {negative_label!r} is refused",
        owned_by_platform(negative_label),
        False,
    )

if failures:
    print("kubernetes namespace ownership test failed:", file=sys.stderr)
    for failure in failures:
        print(" -", failure, file=sys.stderr)
    sys.exit(1)

print("kubernetes namespace ownership tests passed.")
PY
