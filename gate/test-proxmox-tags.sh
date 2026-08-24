#!/usr/bin/env bash
# Focused tests for the Proxmox tag contract.
#
# Two things are proved here and neither can be proved by --syntax-check:
#
#   1. The tag grammar the platform writes and withdraws, exercised through
#      ansible/files/proxmox/guest-app-record.py — the one place that mutates a live guest's
#      tag list. A withdrawal that took ownership, a machine fact or a sibling application
#      with it would be invisible until an operator looked at Proxmox after a removal.
#   2. The tag -> inventory group translation, evaluated as Jinja against the ACTUAL
#      expressions in ansible/inventory/proxmox.yml and ansible/tasks/proxmox/tag-group.yml.
#      Ansible sanitises punctuation out of group names, so `_+lab`, `_-lab` and `_.lab`
#      would collapse into one group under the stock treatment; the inventory translates
#      them deliberately instead, and the lookup seam has to agree with it exactly.
#
# No Proxmox is contacted and no Ansible is started: pure Python against the repository.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

python3 - "$repo" <<'PY'
import importlib.util
import re
import sys
from pathlib import Path

import yaml

try:
    from jinja2 import Environment
    from jinja2.nativetypes import NativeEnvironment
except ImportError:  # pragma: no cover - the gate's own dependency
    print("proxmox-tags test needs jinja2 (it ships with ansible-core)", file=sys.stderr)
    raise SystemExit(1)

repo = Path(sys.argv[1])
failures = []


def check(label, got, want):
    if got != want:
        failures.append("%s\n     expected %r\n          got %r" % (label, want, got))


# -- The tag writer -----------------------------------------------------------
spec = importlib.util.spec_from_file_location(
    "gar", repo / "ansible" / "files" / "proxmox" / "guest-app-record.py"
)
gar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gar)


def deploy(tags, description, instance, kind, url="", date="2026-08-23"):
    before, previous, rows, after = gar.merge_rows(
        description, instance, kind, url, date, False
    )
    return gar.merge_tags(tags, previous, rows), gar.render_description(before, rows, after)


def withdraw(tags, description, instance):
    before, previous, rows, after = gar.merge_rows(description, instance, "", "", "", True)
    return gar.merge_tags(tags, previous, rows), gar.render_description(before, rows, after)


# Ownership is the exact sentinel, and the rendered order is ownership, machine facts,
# topology, applications. Plain lexicographic order IS that order: ASCII puts + (43) before
# - (45) before . (46) before a letter, which is why the grammar was built this way.
check("ownership sentinel", gar.OWNER_TAG, "_+lab")
check(
    "lane order",
    ";".join(sorted(["_sonarr", "_.stack+media", "_-docker", "_+lab", "_-debian"])),
    "_+lab;_-debian;_-docker;_.stack+media;_sonarr",
)

# A native Debian app gets the compact set its defaults declared plus its own tag.
native, native_desc = deploy("_+lab;_-debian;_.shared", "", "caddy", "native", "https://c")
check("native caddy", native, "_+lab;_-debian;_.shared;_caddy")

# A Docker stack host carries Debian, Docker, the stack and the application.
stack_base = "_+lab;_-debian;_-docker;_.stack+media-foxglove;operator-note"
one, desc = deploy(stack_base, "", "sonarr-foxglove", "docker", "https://s")
check(
    "docker stack, one app",
    one,
    "_+lab;_-debian;_-docker;_.stack+media-foxglove;_sonarr-foxglove;operator-note",
)

# Two applications on one stack host coexist.
two, desc = deploy(one, desc, "radarr-foxglove", "docker", "https://r")
check(
    "docker stack, two apps",
    two,
    "_+lab;_-debian;_-docker;_.stack+media-foxglove;_radarr-foxglove;_sonarr-foxglove;"
    "operator-note",
)

# Removing one application preserves its sibling and every stack fact.
after_one, desc = withdraw(two, desc, "sonarr-foxglove")
check(
    "withdraw one app",
    after_one,
    "_+lab;_-debian;_-docker;_.stack+media-foxglove;_radarr-foxglove;operator-note",
)

# Removing the last application leaves the empty stack's identity intact.
empty, desc = withdraw(after_one, desc, "radarr-foxglove")
check(
    "withdraw the last app",
    empty,
    "_+lab;_-debian;_-docker;_.stack+media-foxglove;operator-note",
)
check("emptied region leaves no table", desc, "")

# A re-run against converged state writes nothing at all, tags or notes.
again, again_desc = deploy(stack_base, "", "sonarr-foxglove", "docker", "https://s")
repeat, repeat_desc = deploy(
    again, again_desc, "sonarr-foxglove", "docker", "https://s", date="2027-01-01"
)
check("converged re-run: tags unchanged", repeat, again)
check("converged re-run: notes unchanged", repeat_desc, again_desc)

# A Kubernetes application is stamped on, and withdrawn from, EVERY cluster member.
node_base = "_+lab;_-debian;_-k3s;_.cluster+k3s;_.shared"
nodes = {}
for node in ("k3s-1", "k3s-2", "k3s-3"):
    nodes[node] = deploy(node_base, "", "mixpost-foxglove", "kubernetes", "https://m")
for node, (tags, _desc) in nodes.items():
    check(
        "kubernetes stamp on %s" % node,
        tags,
        "_+lab;_-debian;_-k3s;_.cluster+k3s;_.shared;_mixpost-foxglove",
    )
for node, (tags, node_desc) in nodes.items():
    check(
        "kubernetes withdrawal from %s" % node,
        withdraw(tags, node_desc, "mixpost-foxglove")[0],
        node_base,
    )

# The reserved lanes. An instance name beginning with one would produce a tag in the wrong
# lane, and its withdrawal would then either miss it or delete something the guest needs.
for bad in ("+lab", "-debian", ".template", "_caddy"):
    if not gar.reserved_name(bad):
        failures.append("instance name %r must be refused as a reserved lane" % bad)
for good in ("caddy", "sonarr-foxglove", "9front"):
    if gar.reserved_name(good):
        failures.append("instance name %r must be accepted" % good)

# Nothing is removed on a bare `_` prefix: a platform tag the row set never produced
# survives a withdrawal untouched.
survivor, _survivor_desc = withdraw("_+lab;_-debian;_.template;_stranger", "", "sonarr")
check("no prefix-based deletion", survivor, "_+lab;_-debian;_.template;_stranger")


# -- The tag -> inventory group translation -----------------------------------
env = Environment()
env.filters["regex_replace"] = lambda value, pattern, replacement="": re.sub(
    pattern, replacement, value
)

inventory = yaml.safe_load(
    (repo / "ansible" / "inventory" / "proxmox.yml").read_text(encoding="utf-8")
)
inventory_expr = env.from_string("{{ %s }}" % inventory["compose"]["homelabinfra_groups"])

lookup_doc = yaml.safe_load(
    (repo / "ansible" / "tasks" / "proxmox" / "tag-group.yml").read_text(encoding="utf-8")
)
lookup_expr = env.from_string(lookup_doc[0]["ansible.builtin.set_fact"]["tag_group_name"])

# Punctuation produces exactly these group names, and a tag outside the platform lane
# produces none at all.
expected = {
    "_+lab": "lab_managed",
    "_-debian": "lab_fact_debian",
    "_-ubuntu": "lab_fact_ubuntu",
    "_-docker": "lab_fact_docker",
    "_-k3s": "lab_fact_k3s",
    "_.stack+media": "lab_stack_media",
    "_.stack+media-foxglove": "lab_stack_media_foxglove",
    "_.cluster+k3s": "lab_cluster_k3s",
    "_.template": "lab_template",
    "_.shared": "lab_shared",
    "_caddy": "lab_app_caddy",
    "_sonarr-foxglove": "lab_app_sonarr_foxglove",
    "_mixpost-foxglove": "lab_app_mixpost_foxglove",
    "operator-note": "",
    "homelab-infra": "",
}

for tag, want in expected.items():
    check("lookup seam: %s" % tag, lookup_expr.render(tag_group_tag=tag), want)

# The whole-list expression must agree with the per-tag one, tag for tag. Two
# implementations exist only because the inventory plugin cannot call a task file; they may
# never drift apart.
rendered = eval(inventory_expr.render(proxmox_tags_parsed=list(expected)))  # noqa: S307
check(
    "inventory expression agrees with the lookup seam",
    rendered,
    [name for name in expected.values() if name],
)

# Neither expression may contain a backslash, and this test is the whole reason the two
# above can be trusted. Ansible's Jinja does not process escape sequences inside string
# literals; the stock Jinja rendering them here does. A capture-group replacement therefore
# means one thing to this gate and the opposite thing in production: 'lab_app_\\1' renders
# correctly above and yields the literal text `lab_app_\1` inside the inventory plugin,
# which the group-name sanitiser turns into `lab_app__1` — ONE group holding every
# application in the lab. That shipped, and a fresh lab's first Caddy deploy found the
# runner in `lab_app_caddy` and tried to install Caddy onto the control plane.
#
# Zero-width lookaheads and literal prefixes capture nothing, so nothing has to be spelled
# back, and the two engines cannot disagree.
for label, source in (
    ("inventory expression", inventory["compose"]["homelabinfra_groups"]),
    ("lookup seam", lookup_doc[0]["ansible.builtin.set_fact"]["tag_group_name"]),
):
    if "\\" in source:
        failures.append(
            "%s contains a backslash. Ansible and stock Jinja disagree about escapes in "
            "string literals, so a backreference cannot be verified here. Use a zero-width "
            "lookahead and a literal prefix instead." % label
        )

# Ownership is a group of its own: no machine fact, topology tag or application name may
# land in it, which is what the sanitiser would otherwise have caused.
for tag in ("_-lab", "_.lab", "_lab"):
    if lookup_expr.render(tag_group_tag=tag) == "lab_managed":
        failures.append("%r must not resolve to the ownership group" % tag)

# -- Estate isolation, evaluated against the shipped placement expressions --------
#
# Placement is the enforcement point, not naming: `_stack_id` is the identity BOTH the
# existence lookup and the create path use, so two estates that resolve to different ids
# cannot reach one host however similar their names look.
# A NATIVE environment, because Ansible's templar hands a dict or a bool back to the next
# expression rather than the text of one. Rendering these as strings would make every
# `| length` and `| bool` operate on a repr.
native = NativeEnvironment()
native.filters["regex_replace"] = env.filters["regex_replace"]
native.filters["dict2items"] = lambda mapping: [
    {"key": key, "value": value} for key, value in mapping.items()
]
def combine(base, *others, recursive=False):
    result = dict(base)
    for other in others:
        for key, value in (other or {}).items():
            if recursive and isinstance(result.get(key), dict) and isinstance(value, dict):
                result[key] = combine(result[key], value, recursive=True)
            else:
                result[key] = value
    return result


native.filters["combine"] = combine
native.filters["bool"] = lambda value: (
    value if isinstance(value, bool) else str(value).strip().lower() in ("true", "yes", "on", "1")
)


def run_set_fact(task, context):
    """Render one task's `vars:` in order, then its set_fact values, as Ansible would."""
    scope = dict(context)
    for name, expression in (task.get("vars") or {}).items():
        scope[name] = native.from_string(expression).render(**scope)
    return {
        name: native.from_string(expression).render(**scope)
        for name, expression in task["ansible.builtin.set_fact"].items()
    }


def flatten(tasks):
    """Every task in a file, blocks included — the placement tasks live inside one."""
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always", "tasks"):
            yield from flatten(task.get(key))


def task_named(path, fragment):
    document = yaml.safe_load((repo / path).read_text(encoding="utf-8"))
    for task in flatten(document):
        if fragment in (task.get("name") or ""):
            return task
    raise SystemExit("no task matching %r in %s" % (fragment, path))


place = task_named(
    "ansible/tasks/stack/find-or-create-host.yml", "estate and effective identity"
)
two_estates = {
    "infrastructure": {
        "domains": {
            "personal": {"domain": "personal.example.com", "default": True},
            "foxglove": {"domain": "foxglove.example.com"},
        }
    }
}
one_estate = {"infrastructure": {"domains": {"personal": {"domain": "personal.example.com"}}}}

# Separate estates resolve to separate ordinary stack hosts.
personal = run_set_fact(
    place,
    {"stack_name": "media", "stack_estate": "", "_stack_sizing": {},
     "homelabinfra_config": two_estates},
)
foxglove = run_set_fact(
    place,
    {"stack_name": "media", "stack_estate": "foxglove", "_stack_sizing": {},
     "homelabinfra_config": two_estates},
)
check("default estate stack id", personal["_stack_id"], "media-personal")
check("named estate stack id", foxglove["_stack_id"], "media-foxglove")
if personal["_stack_id"] == foxglove["_stack_id"]:
    failures.append("two estates must not resolve to the same ordinary stack host")

# A single-estate lab is untouched: no suffix appears anywhere.
single = run_set_fact(
    place,
    {"stack_name": "media", "stack_estate": "", "_stack_sizing": {},
     "homelabinfra_config": one_estate},
)
check("single-estate stack id", single["_stack_id"], "media")
check(
    "no domains map at all",
    run_set_fact(
        place,
        {"stack_name": "media", "stack_estate": "", "_stack_sizing": {},
         "homelabinfra_config": {}},
    )["_stack_id"],
    "media",
)

# Explicit sharing, and only explicit sharing, crosses the boundary.
shared = run_set_fact(
    place,
    {"stack_name": "media", "stack_estate": "foxglove", "_stack_sizing": {"shared": True},
     "homelabinfra_config": two_estates},
)
check("shared stack id", shared["_stack_id"], "media")
check("shared flag", shared["_stack_shared"], True)
check("unshared flag", foxglove["_stack_shared"], False)

# ...and `_.shared` is written from that declaration, never from where apps landed.
tag_task = task_named("ansible/tasks/stack/find-or-create-host.yml", "Set stack hostname")
shared_tags = run_set_fact(
    tag_task,
    {"_stack_id": "media", "_stack_shared": True, "_stack_sizing": {},
     "homelabinfra_config": {"proxmox": {"lxc": {"tags": ["_+lab", "_-debian"]}}}},
)
plain_tags = run_set_fact(
    tag_task,
    {"_stack_id": "media-foxglove", "_stack_shared": False, "_stack_sizing": {},
     "homelabinfra_config": {"proxmox": {"lxc": {"tags": ["_+lab", "_-debian"]}}}},
)
check(
    "shared stack host tags",
    sorted(shared_tags["homelabinfra_config"]["proxmox"]["lxc"]["tags"]),
    ["_+lab", "_-debian", "_-docker", "_.shared", "_.stack+media"],
)
check(
    "ordinary stack host tags",
    sorted(plain_tags["homelabinfra_config"]["proxmox"]["lxc"]["tags"]),
    ["_+lab", "_-debian", "_-docker", "_.stack+media-foxglove"],
)
check(
    "ordinary stack hostname",
    plain_tags["homelabinfra_config"]["proxmox"]["lxc"]["hostname"],
    "stack-media-foxglove",
)

# Removal resolves the same identity the deploy did, or it would empty another estate's
# host — or none at all.
remove = task_named(
    "ansible/playbooks/apps/remove.yml", "stack identity this app was placed on"
)
removed = run_set_fact(
    remove,
    {"app_config": {"stack": "media", "routing": {"estate": "foxglove"}},
     "homelabinfra_config": two_estates,
     "_rm_stack_defaults_file": {"stack_defaults": {}}},
)
check("removal stack id", removed["_rm_stack_id"], foxglove["_stack_id"])

# -- Selection and exclusion, read off the shipped files ------------------------
#
# Templates carry the ownership tag like everything else this platform creates, so the ONE
# thing that keeps them out of every guest-wide job is the inventory filter. Asserted here
# because its absence is silent: jobs simply start failing against a VM that never boots.
if "not (proxmox_template | default(false) | bool)" not in [
    str(item) for item in inventory.get("filters", [])
]:
    failures.append("inventory must filter templates out of every group")

# The guest-wide consumers select the managed-guest group derived from exact `_+lab`
# membership. PBS and the node descent read the tag directly, because they run against the
# Proxmox API rather than through the inventory.
selectors = {
    "ansible/playbooks/maintenance/status.yml": "lab_managed",
    "ansible/playbooks/maintenance/guest-maintenance.yml": "lab_managed",
    "ansible/playbooks/maintenance/verify-ascent.yml": "lab_managed",
    "ansible/playbooks/maintenance/check-native-updates.yml": "lab_managed",
    "ansible/roles/observability/tasks/main.yml": "lab_managed",
    "ansible/tasks/bootstrap/configure-pbs.yml": "'_+lab'",
    "ansible/tasks/maintenance/arm-node-descent.yml": '"_+lab"',
}
for path, needle in selectors.items():
    if needle not in (repo / path).read_text(encoding="utf-8"):
        failures.append("%s must select managed guests via %s" % (path, needle))

# Nothing active may still depend on the tag forms this contract replaced. Scoped to the
# code and the normative documents: docs/meta/ is history and is deliberately left alone.
retired = {
    "tag_homelab_infra": "the old ownership group",
    "app_<instance>": "the old application tag",
    "kind_docker": "the old install-kind tag",
    "kind_native": "the old install-kind tag",
    "role_template": "the old template facet tag",
    "media_stack": "the old suffixed stack identifier",
    "sso_stack": "the old suffixed stack identifier",
    "monitoring_stack": "the old suffixed stack identifier",
}
# Anchored on a non-word boundary so the replacements do not match themselves:
# `lab_app_<instance>` is the new group name and must not read as the old tag.
retired_res = {
    token: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(token)) for token in retired
}
scanned = [
    path
    for pattern in ("ansible/**/*", "rundeck/**/*", "gate/*", "catalog/*",
                    "config.example/**/*", "docs/specs/*", "AGENTS.md", "docs/architecture.md")
    for path in repo.glob(pattern)
    if path.is_file()
    and path.suffix in (".yml", ".yaml", ".py", ".sh", ".md", ".j2", ".json", "")
    and "docs/meta/" not in path.as_posix()
    # This file names every retired form on purpose; it is the one exemption.
    and path.name != "test-proxmox-tags.sh"
]
for path in scanned:
    try:
        body = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for token, why in retired.items():
        if retired_res[token].search(body):
            failures.append(
                "%s still contains %r (%s)" % (path.relative_to(repo).as_posix(), token, why)
            )

if failures:
    print("proxmox-tags test failed:", file=sys.stderr)
    for failure in failures:
        print("  - %s" % failure, file=sys.stderr)
    raise SystemExit(1)

print("proxmox tag contract tests passed.")
PY
