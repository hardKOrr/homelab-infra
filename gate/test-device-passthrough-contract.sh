#!/usr/bin/env bash
# Focused tests for the device-passthrough contract (docs/specs/device-passthrough.md).
#
# None of the following can be proved by --syntax-check, and all of it is evaluated against
# the ACTUAL expressions in the task files, not a re-implementation of them:
#
#   1. The ownership guard: a guest missing the `_+lab` tag must be refused by the PCI and
#      USB passthrough seams.
#   2. The dedicated-device conflict check: a device already claimed by a DIFFERENT guest
#      must be refused, a device already held by the SAME guest must pass (idempotent
#      no-op), and a free device must pass.
#   3. USB identity is a Proxmox resource mapping, not a raw vendor:product pair, which
#      cannot distinguish two identical physical devices.
#   4. Detach matches only the binding whose content (host path / PCI id / USB mapping)
#      was requested — an unrelated devN/hostpciN/usbN entry, or a raw non-mapping usbN
#      entry this platform never wrote, is never selected for removal.
#
# No Proxmox is contacted and no Ansible is started: pure Python against the repository.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

python="$HOME/.venvs/homelab-ansible/bin/python"
if [ ! -x "$python" ]; then
    python="python3"
fi
ansible_playbook="$HOME/.venvs/homelab-ansible/bin/ansible-playbook"
if [ ! -x "$ansible_playbook" ]; then
    ansible_playbook="$(command -v ansible-playbook || true)"
fi

"$python" - "$repo" "$ansible_playbook" <<'PY'
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

try:
    from jinja2.nativetypes import NativeEnvironment
except ImportError:  # pragma: no cover - the gate's own dependency
    print("device-passthrough test needs jinja2 (it ships with ansible-core)", file=sys.stderr)
    raise SystemExit(1)

repo = Path(sys.argv[1])
ansible_playbook = sys.argv[2]
if not ansible_playbook:
    print("device-passthrough test needs ansible-playbook (see gate/README.md bootstrap)",
          file=sys.stderr)
    raise SystemExit(1)
failures = []

env = NativeEnvironment()
env.filters["dict2items"] = lambda mapping: [
    {"key": key, "value": value} for key, value in mapping.items()
]
env.filters["combine"] = lambda base, *others: {
    **base, **{k: v for other in others for k, v in (other or {}).items()}
}
env.filters["regex_replace"] = lambda value, pattern, repl="": re.sub(pattern, repl, value)
env.filters["regex_escape"] = lambda value: re.escape(value)
env.tests["contains"] = lambda seq, value: value in seq
env.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
env.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None


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
    "USB: unclaimed mapping passes",
    eval_assert(usb_task, {
        "_usb_vmid": "410",
        "usb_passthrough_device": {"mapping": "ha-zigbee"},
        "_usb_claims_by_vmid": {"410": []},
    }),
    True,
)
check(
    "USB: mapping already held by the SAME guest is a no-op",
    eval_assert(usb_task, {
        "_usb_vmid": "410",
        "usb_passthrough_device": {"mapping": "ha-zigbee"},
        "_usb_claims_by_vmid": {"410": ["ha-zigbee"]},
    }),
    True,
)
check(
    "USB: mapping held by a DIFFERENT guest is refused",
    eval_assert(usb_task, {
        "_usb_vmid": "410",
        "usb_passthrough_device": {"mapping": "ha-zigbee"},
        "_usb_claims_by_vmid": {"410": [], "999": ["ha-zigbee"]},
    }),
    False,
)

# -- USB identity must be a resource mapping, never a raw vendor:product pair -------------
usb_input_task = task_named(
    "ansible/tasks/proxmox/attach-usb-passthrough.yml", "Assert required input"
)
if "usb_passthrough_device.mapping" not in str(usb_input_task["ansible.builtin.assert"]["that"]):
    failures.append(
        "attach-usb-passthrough.yml must require usb_passthrough_device.mapping — a "
        "vendor:product pair does not identify one physical device among duplicates"
    )


# The detach set_fact expressions below use \1/\2 regex backreferences. Ansible's own
# Jinja environment renders those correctly; stock jinja2 (used for eval_assert above)
# treats a bare backslash-digit as an octal escape and mangles it into a control
# character. Rather than re-implement ansible's escaping, these are run through the real
# ansible-playbook binary against localhost — no Proxmox, no network.
def eval_set_fact(task, context):
    """Run a single set_fact task through real ansible-playbook and return its result."""
    fact_name = next(iter(task["ansible.builtin.set_fact"]))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.json"
        playbook = [{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": context,
            "tasks": [
                task,
                {
                    "name": "capture",
                    "ansible.builtin.copy": {
                        "dest": str(out),
                        "content": "{{ %s | to_json }}" % fact_name,
                    },
                },
            ],
        }]
        playbook_path = Path(tmp) / "playbook.yml"
        playbook_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")
        result = subprocess.run(
            [ansible_playbook, "-i", "localhost,", "-c", "local", str(playbook_path)],
            cwd=tmp,
            env={"ANSIBLE_CONFIG": "", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemExit(
                "ansible-playbook failed evaluating %s:\n%s\n%s"
                % (fact_name, result.stdout, result.stderr)
            )
        return {fact_name: json.loads(out.read_text(encoding="utf-8"))}


# -- Detach: only the named binding is matched, everything else is left alone -------------
dsd_task = task_named(
    "ansible/tasks/proxmox/detach-shared-device.yml",
    "Resolve which requested devices are bound, and at what index",
)
check(
    "detach shared device: matches only the requested host path, not an unrelated dev0",
    eval_set_fact(dsd_task, {
        "shared_device_list": [{"host": "/dev/dri/renderD128"}],
        "_dsd_config": {"stdout_lines": [
            "dev0: path=/dev/dri/renderD128,mode=0666",
            "dev1: path=/dev/ttyUSB3,mode=0660",
        ]},
    })["_dsd_matched_indices"],
    ["0"],
)
check(
    "detach shared device: no-op when the requested device is absent",
    eval_set_fact(dsd_task, {
        "shared_device_list": [{"host": "/dev/dri/renderD128"}],
        "_dsd_config": {"stdout_lines": ["dev0: path=/dev/ttyUSB3,mode=0660"]},
    })["_dsd_matched_indices"],
    [],
)

dpci_task = task_named(
    "ansible/tasks/proxmox/detach-pci-passthrough.yml",
    "Resolve whether the device is assigned here, and at what index",
)
check(
    "detach PCI passthrough: matches only the requested id, not an unrelated hostpci0",
    eval_set_fact(dpci_task, {
        "pci_passthrough_device": {"id": "0000:01:00.0"},
        "_dpci_config": {"stdout_lines": [
            "hostpci0: 0000:02:00.0,pcie=1",
            "hostpci1: 0000:01:00.0,pcie=1",
        ]},
    })["_dpci_matched_index"],
    "1",
)
check(
    "detach PCI passthrough: no-op when the requested device is absent",
    eval_set_fact(dpci_task, {
        "pci_passthrough_device": {"id": "0000:01:00.0"},
        "_dpci_config": {"stdout_lines": ["hostpci0: 0000:02:00.0,pcie=1"]},
    })["_dpci_matched_index"],
    "",
)

dusb_task = task_named(
    "ansible/tasks/proxmox/detach-usb-passthrough.yml",
    "Resolve whether the mapping is assigned here, and at what index",
)
check(
    "detach USB passthrough: matches only the requested mapping, not an unrelated usb0",
    eval_set_fact(dusb_task, {
        "usb_passthrough_device": {"mapping": "ha-zigbee"},
        "_dusb_config": {"stdout_lines": [
            "usb0: mapping=some-other-device",
            "usb1: mapping=ha-zigbee",
        ]},
    })["_dusb_matched_index"],
    "1",
)
check(
    "detach USB passthrough: no-op when the requested mapping is absent",
    eval_set_fact(dusb_task, {
        "usb_passthrough_device": {"mapping": "ha-zigbee"},
        "_dusb_config": {"stdout_lines": ["usb0: mapping=some-other-device"]},
    })["_dusb_matched_index"],
    "",
)

# A raw host= USB entry this platform never wrote (no mapping=) must never be touched by
# the mapping-keyed detach match, even if it happens to share the guest.
check(
    "detach USB passthrough: a raw host= entry outside this contract is never matched",
    eval_set_fact(dusb_task, {
        "usb_passthrough_device": {"mapping": "ha-zigbee"},
        "_dusb_config": {"stdout_lines": ["usb0: host=0483:5740"]},
    })["_dusb_matched_index"],
    "",
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
