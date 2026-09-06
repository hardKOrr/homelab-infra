#!/usr/bin/env bash
# Focused tests for the device-passthrough contract (docs/specs/device-passthrough.md).
#
# Two things are proved here and neither can be proved by --syntax-check:
#
#   1. The ownership guard: a guest missing the `_+lab` tag must be refused by the PCI and
#      USB passthrough seams, evaluated against the ACTUAL assert expressions in
#      ansible/tasks/proxmox/attach-pci-passthrough.yml and attach-usb-passthrough.yml.
#   2. The dedicated-device conflict check: a device already claimed by a DIFFERENT guest
#      must be refused, a device already held by the SAME guest must pass (idempotent
#      no-op), and a free device must pass — evaluated the same way.
#
# No Proxmox is contacted and no Ansible is started: pure Python against the repository.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

python3 - "$repo" <<'PY'
import sys
from pathlib import Path

import yaml

try:
    from jinja2.nativetypes import NativeEnvironment
except ImportError:  # pragma: no cover - the gate's own dependency
    print("device-passthrough test needs jinja2 (it ships with ansible-core)", file=sys.stderr)
    raise SystemExit(1)

repo = Path(sys.argv[1])
failures = []

env = NativeEnvironment()
env.filters["dict2items"] = lambda mapping: [
    {"key": key, "value": value} for key, value in mapping.items()
]
env.filters["combine"] = lambda base, *others: {
    **base, **{k: v for other in others for k, v in (other or {}).items()}
}
env.tests["contains"] = lambda seq, value: value in seq


def flatten(tasks):
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


def eval_assert(task, context):
    """Render a task's `vars:` in order, then evaluate its `that:` as ansible would."""
    scope = dict(context)
    for name, expression in (task.get("vars") or {}).items():
        scope[name] = env.from_string(expression).render(**scope)
    that = task["ansible.builtin.assert"]["that"]
    if isinstance(that, str):
        that = [that]
    return all(bool(env.from_string("{{ %s }}" % cond).render(**scope)) for cond in that)


def check(label, got, want):
    if got != want:
        failures.append("%s: expected %r, got %r" % (label, want, got))


# -- Ownership guard, PCI and USB -------------------------------------------------------
for path, task_name, tags_var in (
    ("ansible/tasks/proxmox/attach-pci-passthrough.yml",
     "Assert the guest carries the ownership tag", "_pci_tags"),
    ("ansible/tasks/proxmox/attach-usb-passthrough.yml",
     "Assert the guest carries the ownership tag", "_usb_tags"),
):
    task = task_named(path, task_name)
    check(
        "%s: managed guest passes" % path,
        eval_assert(task, {tags_var: ["_+lab", "_-debian"]}),
        True,
    )
    check(
        "%s: untagged/foreign guest is refused" % path,
        eval_assert(task, {tags_var: ["_-debian"]}),
        False,
    )

# -- Dedicated-device conflict check, PCI ------------------------------------------------
pci_task = task_named(
    "ansible/tasks/proxmox/attach-pci-passthrough.yml",
    "Assert the device is free, or already held by this guest",
)
check(
    "PCI: unclaimed device passes",
    eval_assert(pci_task, {
        "_pci_vmid": "201",
        "pci_passthrough_device": {"id": "0000:01:00.0"},
        "_pci_claims_by_vmid": {"201": [], "305": ["0000:02:00.0"]},
    }),
    True,
)
check(
    "PCI: device already held by the SAME guest is a no-op",
    eval_assert(pci_task, {
        "_pci_vmid": "201",
        "pci_passthrough_device": {"id": "0000:01:00.0"},
        "_pci_claims_by_vmid": {"201": ["0000:01:00.0"]},
    }),
    True,
)
check(
    "PCI: device held by a DIFFERENT guest is refused",
    eval_assert(pci_task, {
        "_pci_vmid": "201",
        "pci_passthrough_device": {"id": "0000:01:00.0"},
        "_pci_claims_by_vmid": {"201": [], "305": ["0000:01:00.0"]},
    }),
    False,
)

# -- Dedicated-device conflict check, USB -------------------------------------------------
usb_task = task_named(
    "ansible/tasks/proxmox/attach-usb-passthrough.yml",
    "Assert the device is free, or already held by this guest",
)
check(
    "USB: unclaimed device passes",
    eval_assert(usb_task, {
        "_usb_vmid": "410",
        "usb_passthrough_device": {"id": "0483:5740"},
        "_usb_claims_by_vmid": {"410": []},
    }),
    True,
)
check(
    "USB: device already held by the SAME guest is a no-op",
    eval_assert(usb_task, {
        "_usb_vmid": "410",
        "usb_passthrough_device": {"id": "0483:5740"},
        "_usb_claims_by_vmid": {"410": ["0483:5740"]},
    }),
    True,
)
check(
    "USB: device held by a DIFFERENT guest is refused",
    eval_assert(usb_task, {
        "_usb_vmid": "410",
        "usb_passthrough_device": {"id": "0483:5740"},
        "_usb_claims_by_vmid": {"410": [], "999": ["0483:5740"]},
    }),
    False,
)

# -- Shared devices never run this ownership/conflict logic at all ------------------------
# Confirms the shared seam has no dedicated-device conflict assertion of its own — sharing
# one device across guests is the whole point of that mode.
shared_doc = yaml.safe_load(
    (repo / "ansible/tasks/proxmox/attach-shared-device.yml").read_text(encoding="utf-8")
)
shared_names = [task.get("name", "") for task in flatten(shared_doc)]
if any("already held by" in name for name in shared_names):
    failures.append(
        "attach-shared-device.yml must not carry a dedicated-device conflict check; "
        "shared devices are meant to be bound into several guests at once"
    )
if not any("ownership tag" in name for name in shared_names):
    failures.append("attach-shared-device.yml must still assert the _+lab ownership tag")

if failures:
    print("device-passthrough contract test failed:", file=sys.stderr)
    for failure in failures:
        print("  - %s" % failure, file=sys.stderr)
    raise SystemExit(1)

print("device-passthrough contract tests passed.")
PY
