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
block = block_task["block"]

# Structural guard, not just an expression check: rendering the assert's own condition in
# isolation would still pass a review that moved a destructive task ahead of it, or made
# the assert conditional so a future edit could skip it. The assert is found by scanning
# the task list — not assumed to be block[0] — so that check itself fails loudly if the
# assert is no longer the first task, rather than grabbing whatever task happens to sit
# there and silently evaluating the wrong condition.
#
# A task "mutates" here if its argv names one of kubectl's own mutating verbs (delete,
# patch) — the same vocabulary unwiring/kubernetes.yml's tasks use, rather than a
# heuristic keyword search over task names, which a rename could silently defeat.
MUTATING_VERBS = {"delete", "patch"}


def task_argv(task):
    cmd = task.get("ansible.builtin.command")
    if isinstance(cmd, dict):
        return cmd.get("argv") or []
    return []


def is_mutating(task):
    return any(verb in task_argv(task) for verb in MUTATING_VERBS)


assert_index = next(
    i for i, t in enumerate(block) if "ansible.builtin.assert" in t
)
check("the ownership assert is the block's first task", assert_index, 0)
check(
    "the ownership assert task itself carries no `when` that could skip it",
    "when" in block[assert_index],
    False,
)

mutating_indices = [i for i, t in enumerate(block) if is_mutating(t)]
check("the unwiring block still has mutating (delete/patch) task(s) to guard", bool(mutating_indices), True)
for i in mutating_indices:
    if i <= assert_index:
        failures.append(
            "mutating task %r (index %d) does not come after the ownership assert (index %d)"
            % (block[i].get("name"), i, assert_index)
        )


owner_condition = block[assert_index]["ansible.builtin.assert"]["that"]


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
