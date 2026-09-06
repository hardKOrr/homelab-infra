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
#   5. Every detach seam refuses a guest missing the `_+lab` ownership tag, exactly like
#      attach.
#   6. A content match is never enough for detach to treat a binding as project-owned: only
#      a match paired with the `_.dev+<slug>` provenance tag attach wrote is removed. A
#      content match with no provenance tag — an operator's own identical entry — is
#      classified as NOT owned and left in place.
#
# No Proxmox is contacted. Simple assert-only checks are evaluated with a standalone Jinja
# environment; checks needing ansible's own \1/\2 regex-backreference and native-typing
# behavior run through the real ansible-playbook binary against localhost (see
# eval_set_fact below) — still no Proxmox, no network.
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


# Ansible ANDs a block's own `when:` onto every task inside its block/rescue/always,
# whether or not that task also carries its own `when:`. Tests that inspect a task's
# effective condition need that inherited half, not just what the task node itself
# carries, so `flatten` folds a parent block's `when:` into each descendant task.
def flatten(tasks, inherited_when=None):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        if inherited_when is not None and "block" not in task:
            own_when = task.get("when")
            if own_when is None:
                task = {**task, "when": inherited_when}
            else:
                combined = (own_when if isinstance(own_when, list) else [own_when])
                combined = combined + (inherited_when if isinstance(inherited_when, list)
                                        else [inherited_when])
                task = {**task, "when": combined}
        yield task
        block_when = task.get("when") if "block" in task else inherited_when
        for key in ("block", "rescue", "always", "tasks"):
            yield from flatten(task.get(key), inherited_when=block_when)


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
    """Run a single set_fact task through real ansible-playbook and return every fact it sets."""
    fact_names = list(task["ansible.builtin.set_fact"])
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
                        "content": "{{ %s | to_json }}" % (
                            "{" + ", ".join("'%s': %s" % (n, n) for n in fact_names) + "}"
                        ),
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
                % (fact_names, result.stdout, result.stderr)
            )
        return json.loads(out.read_text(encoding="utf-8"))


# -- Attach must never tag a binding that already existed before this run -----------------
# The provenance tag is only trustworthy if it is written exactly when, and only when, THIS
# run performs the bind/assignment. If attach re-tagged an already-present device on every
# rerun, an operator's own pre-existing, untagged entry would be silently adopted the next
# time attach happened to run — which is exactly the gap this regression test closes.

# PCI and USB: the single combined "assign + tag" task must be gated on the same
# `not _..._already_present` condition that gates the assignment itself — there is no
# separate tag-write path that could diverge from it.
for path, task_name, present_var in (
    ("ansible/tasks/proxmox/attach-pci-passthrough.yml",
     "PCI passthrough | Assign the device and its provenance tag", "_pci_already_present"),
    ("ansible/tasks/proxmox/attach-usb-passthrough.yml",
     "USB passthrough | Assign the device and its provenance tag", "_usb_already_present"),
):
    task = task_named(path, task_name)
    when = task.get("when")
    if isinstance(when, list):
        when = " and ".join(str(w) for w in when)
    if ("not %s" % present_var) not in str(when):
        failures.append(
            "%s: %r must run only when the device was NOT already present "
            "(when=%r) — otherwise a rerun would retroactively tag a pre-existing, "
            "possibly operator-created, entry" % (path, task_name, when)
        )
    if "-tags" not in task["ansible.builtin.command"]["cmd"]:
        failures.append(
            "%s: %r must write -tags in the SAME command as the assignment, so a bind "
            "and its provenance tag land atomically" % (path, task_name)
        )

# Shared device: the combined bind+tag command must build its tag list from _sd_missing
# (devices THIS run is about to bind), never from the full shared_device_list — otherwise
# an already-bound, untagged device would be tagged just because it was named again.
sd_bind_task = task_named(
    "ansible/tasks/proxmox/attach-shared-device.yml",
    "Bind the missing devices and their provenance tags in one call",
)
sd_bind_cmd = sd_bind_task["ansible.builtin.command"]["cmd"]
if "_sd_missing" not in sd_bind_cmd or "shared_device_list" in sd_bind_cmd:
    failures.append(
        "attach-shared-device.yml's bind+tag command must be built from _sd_missing "
        "only, not the full shared_device_list, or an already-bound device would be "
        "retagged on every rerun"
    )
sd_tags_task = task_named(
    "ansible/tasks/proxmox/attach-shared-device.yml",
    "Resolve the provenance tags for the devices being bound this run",
)
sd_tags_expr = sd_tags_task["ansible.builtin.set_fact"]["_sd_new_provenance_tags"]
if "_sd_missing" not in sd_tags_expr or "shared_device_list" in sd_tags_expr:
    failures.append(
        "attach-shared-device.yml must compute _sd_new_provenance_tags from _sd_missing "
        "only — tagging every requested host, including ones already bound, would adopt "
        "pre-existing operator-created binds on a rerun"
    )
check(
    "shared device: provenance tags cover only the device THIS run binds, not one already present",
    eval_set_fact(sd_tags_task, {
        "_sd_missing": [{"host": "/dev/dri/renderD128"}],
    })["_sd_new_provenance_tags"],
    ["_.dev+dev-dri-renderd128"],
)

# -- PCIe stop/configure/start must restore the guest on failure, not just on success ------
# PCIe passthrough cannot be hotplugged, so attach and detach stop a running VM before
# changing hostpciN. If the `qm set` itself then fails, the earlier (unwrapped) version of
# this file aborted before ever reaching "Start the guest again", leaving a previously
# running VM stopped. The fix wraps stop/configure/start in a block with a rescue path that
# restarts the guest and re-raises the original error — checked here structurally, since
# actually failing `qm set` would require a live Proxmox node.
for path, block_name, restore_name, was_running_var in (
    ("ansible/tasks/proxmox/attach-pci-passthrough.yml",
     "PCI passthrough | Stop, assign, and restart the guest",
     "PCI passthrough | Restore the guest to running after a failed assignment",
     "_pci_was_running"),
    ("ansible/tasks/proxmox/detach-pci-passthrough.yml",
     "Detach PCI passthrough | Stop, remove, and restart the guest",
     "Detach PCI passthrough | Restore the guest to running after a failed removal",
     "_dpci_was_running"),
):
    document = yaml.safe_load((repo / path).read_text(encoding="utf-8"))
    block_task = next(
        (t for t in document if isinstance(t, dict) and t.get("name") == block_name), None
    )
    if block_task is None or "block" not in block_task or "rescue" not in block_task:
        failures.append(
            "%s: %r must be a block/rescue task so a failure after the guest is "
            "stopped restores it instead of leaving it down" % (path, block_name)
        )
        continue
    rescue_names = [t.get("name", "") for t in block_task["rescue"]]
    if restore_name not in rescue_names:
        failures.append(
            "%s: rescue must include %r to restart the guest before re-raising the "
            "original failure" % (path, restore_name)
        )
    restore_task = next(
        (t for t in block_task["rescue"] if t.get("name") == restore_name), None
    )
    if restore_task is not None:
        if was_running_var not in str(restore_task.get("when", "")):
            failures.append(
                "%s: %r must be gated on %s, so a guest that was already stopped "
                "before the run is not started" % (path, restore_name, was_running_var)
            )
        if restore_task.get("ansible.builtin.command", {}).get("cmd", "").split()[0:2] \
                != ["qm", "start"]:
            failures.append(
                "%s: %r must run 'qm start' to restore the guest" % (path, restore_name)
            )
    fail_task = block_task["rescue"][-1] if block_task["rescue"] else {}
    if "ansible.builtin.fail" not in fail_task or \
            "ansible_failed_result" not in str(fail_task.get("ansible.builtin.fail", {})):
        failures.append(
            "%s: rescue must end by re-raising the original failure (referencing "
            "ansible_failed_result), not swallowing it" % path
        )

# -- Detach: ownership guard on every detach seam, exactly like attach --------------------
for path, tags_var in (
    ("ansible/tasks/proxmox/detach-shared-device.yml", "_dsd_tags"),
    ("ansible/tasks/proxmox/detach-pci-passthrough.yml", "_dpci_tags"),
    ("ansible/tasks/proxmox/detach-usb-passthrough.yml", "_dusb_tags"),
):
    task = task_named(path, "Assert the guest carries the ownership tag")
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

# -- Detach: content match alone is never ownership; the provenance tag decides -----------
dsd_loop_task = task_named(
    "ansible/tasks/proxmox/detach-shared-device.yml",
    "Resolve bound state, provenance tag, and ownership per device",
)
results = eval_set_fact(dsd_loop_task, {
    "shared_device_list": [
        {"host": "/dev/dri/renderD128"},  # bound, owned
        {"host": "/dev/ttyUSB3"},         # bound, but NOT ours (no provenance tag)
        {"host": "/dev/dri/renderD129"},  # never bound at all
    ],
    "_dsd_config": {"stdout_lines": [
        "dev0: path=/dev/dri/renderD128,mode=0666",
        "dev1: path=/dev/ttyUSB3,mode=0660",
    ]},
    "_dsd_tags": ["_+lab", "_.dev+dev-dri-renderd128"],
})["_dsd_results"]
by_host = {r["host"]: r for r in results}
check("shared device: bound + provenance tag present -> owned",
      by_host["/dev/dri/renderD128"]["owned"], "yes")
check("shared device: bound but NO provenance tag -> not owned, never adopted",
      by_host["/dev/ttyUSB3"]["owned"], "no")
check("shared device: never bound -> absent (empty index)",
      by_host["/dev/dri/renderD129"]["index"], "")

dsd_split_task = task_named(
    "ansible/tasks/proxmox/detach-shared-device.yml",
    "Split results into owned, unowned, and absent",
)
split = eval_set_fact(dsd_split_task, {"_dsd_results": [
    {"host": "a", "index": "0", "tag": "_.dev+a", "owned": "yes"},
    {"host": "b", "index": "1", "tag": "_.dev+b", "owned": "no"},
    {"host": "c", "index": "", "tag": "_.dev+c", "owned": "no"},
]})
check("shared device split: owned", [x["host"] for x in split["_dsd_owned"]], ["a"])
check("shared device split: unowned (bound, not ours) is reported, not deleted",
      [x["host"] for x in split["_dsd_unowned"]], ["b"])
check("shared device split: absent", [x["host"] for x in split["_dsd_absent"]], ["c"])

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

dpci_owned_task = task_named(
    "ansible/tasks/proxmox/detach-pci-passthrough.yml",
    "Resolve whether this platform actually owns the assignment",
)
check(
    "PCI: assigned here AND provenance tag present -> owned",
    eval_set_fact(dpci_owned_task, {
        "pci_passthrough_device": {"id": "0000:01:00.0"},
        "_dpci_matched_index": "1",
        "_dpci_tags": ["_+lab", "_.dev+0000-01-00-0"],
    })["_dpci_owned"],
    True,
)
check(
    "PCI: assigned here but NO provenance tag -> not owned, never adopted",
    eval_set_fact(dpci_owned_task, {
        "pci_passthrough_device": {"id": "0000:01:00.0"},
        "_dpci_matched_index": "1",
        "_dpci_tags": ["_+lab"],
    })["_dpci_owned"],
    False,
)
check(
    "PCI: not assigned here at all -> not owned regardless of tags",
    eval_set_fact(dpci_owned_task, {
        "pci_passthrough_device": {"id": "0000:01:00.0"},
        "_dpci_matched_index": "",
        "_dpci_tags": ["_+lab", "_.dev+0000-01-00-0"],
    })["_dpci_owned"],
    False,
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

dusb_owned_task = task_named(
    "ansible/tasks/proxmox/detach-usb-passthrough.yml",
    "Resolve whether this platform actually owns the assignment",
)
check(
    "USB: assigned here AND provenance tag present -> owned",
    eval_set_fact(dusb_owned_task, {
        "usb_passthrough_device": {"mapping": "ha-zigbee"},
        "_dusb_matched_index": "1",
        "_dusb_tags": ["_+lab", "_.dev+ha-zigbee"],
    })["_dusb_owned"],
    True,
)
check(
    "USB: assigned here but NO provenance tag -> not owned, never adopted",
    eval_set_fact(dusb_owned_task, {
        "usb_passthrough_device": {"mapping": "ha-zigbee"},
        "_dusb_matched_index": "1",
        "_dusb_tags": ["_+lab"],
    })["_dusb_owned"],
    False,
)
check(
    "USB: not assigned here at all -> not owned regardless of tags",
    eval_set_fact(dusb_owned_task, {
        "usb_passthrough_device": {"mapping": "ha-zigbee"},
        "_dusb_matched_index": "",
        "_dusb_tags": ["_+lab", "_.dev+ha-zigbee"],
    })["_dusb_owned"],
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
