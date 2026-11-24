#!/usr/bin/env python3
"""GPU-backed stack apps resolve to their device owner's shared VM."""

from pathlib import Path
import os
import subprocess

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from remove_plan import load_playbook


ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def render(expression, **variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def walk_tasks(value):
    if isinstance(value, dict):
        if "name" in value:
            yield value
        for child in value.values():
            yield from walk_tasks(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_tasks(child)


def named(tasks, name):
    return next(task for task in walk_tasks(tasks) if task.get("name") == name)


def effective_stack(config, device):
    tasks = read_yaml("ansible/tasks/stack/resolve-app-stack.yml")
    context = {
        "stack_resolution_config": config,
        "stack_resolution_default_name": config.get("stack", ""),
        "homelabinfra_config": {"proxmox": {"devices": device}},
    }
    facts = tasks[0]["ansible.builtin.set_fact"]
    context["_stack_resolution_device"] = render(
        facts["_stack_resolution_device"], **context
    )
    context["_stack_resolution_base_name"] = render(
        facts["_stack_resolution_base_name"], **context
    )
    return render(
        tasks[1]["ansible.builtin.set_fact"]["_stack_resolution_name"], **context
    )


def test_effective_stack_resolution():
    devices = {
        "intel-igpu": {"stack": "media"},
        "second-igpu": {"stack": "second-media"},
        "ollama-gpu": {},
    }
    assert effective_stack(
        {"stack": "photos", "app": {"gpu_device": "intel-igpu"}}, devices
    ) == "media"
    assert effective_stack(
        {"stack": "photos", "app": {"gpu_device": "second-igpu"}}, devices
    ) == "second-media"
    assert effective_stack(
        {"stack": "photos", "app": {"gpu_device": "ollama-gpu"}}, devices
    ) == "ollama-gpu"
    assert effective_stack(
        {"stack": "media", "app": {"gpu_device": ""}}, devices
    ) == "media"
    # Apps with a dedicated GPU VM have no `stack`; their placement path is unchanged.
    assert effective_stack(
        {"hosting": "docker", "app": {"gpu_device": "ollama-gpu"}}, devices
    ) == ""


def test_deploy_and_recovery_paths_share_the_resolver():
    deploy = read_yaml("ansible/tasks/stack/find-or-create-host.yml")
    resolver = named(deploy, "Resolve app stack for the declared GPU")
    resolver_path = resolver["ansible.builtin.include_tasks"]
    assert resolver_path == "../../tasks/stack/resolve-app-stack.yml"
    assert (
        ROOT / "ansible/playbooks/apps" / resolver_path
    ).resolve() == (ROOT / "ansible/tasks/stack/resolve-app-stack.yml").resolve()
    sizing = named(deploy, "Resolve this stack's host sizing")
    assert "_defaults[_stack_resolution_name]" in sizing["ansible.builtin.set_fact"][
        "_stack_sizing"
    ]
    assert "_overrides[_stack_resolution_name]" in sizing["ansible.builtin.set_fact"][
        "_stack_sizing"
    ]

    for path, needle in (
        ("ansible/tasks/maintenance/resolve-app-target.yml", "../stack/resolve-app-stack.yml"),
        ("ansible/tasks/removal/resolve-instance.yml", "../stack/resolve-app-stack.yml"),
        ("ansible/tasks/recovery/restore-app-load.yml", "../stack/resolve-app-stack.yml"),
    ):
        assert needle in (ROOT / path).read_text(encoding="utf-8"), path


def test_removal_keeps_a_gpu_used_by_sibling_apps():
    remove = load_playbook(ROOT / "ansible/playbooks/apps/remove.yml")
    sibling_task = named(remove, "Resolve whether this stack host still has sibling applications")
    expression = sibling_task["ansible.builtin.set_fact"]["_rm_stack_has_other_apps"]

    def has_sibling(tags):
        return render(
            expression,
            _rm_hosts=["stack-media"],
            hostvars={"stack-media": {"proxmox_tags_parsed": tags}},
            instance="immich",
        )

    assert has_sibling(["_+lab", "_-docker", "_.stack+media", "_immich", "_plex"])
    assert not has_sibling(["_+lab", "_-docker", "_.stack+photos", "_immich"])

    detach_immich = named(read_yaml("ansible/roles/immich/tasks/remove-devices.yml"),
                          "Detach Immich's project-owned PCI iGPU")
    detach_frigate = named(read_yaml("ansible/roles/frigate/tasks/remove-devices.yml"),
                           "Detach Frigate's project-owned PCI iGPU")
    for task in (detach_immich, detach_frigate):
        conditions = task["when"] if isinstance(task["when"], list) else [task["when"]]
        assert any("_rm_stack_has_other_apps" in condition for condition in conditions)


def test_provision_tag_stops_before_app_deployment():
    result = subprocess.run(
        [os.environ.get("GATE_ANSIBLE_PLAYBOOK", "ansible-playbook"),
         "-i", "localhost,", "playbooks/apps/jellyfin.yml",
         "-e", "instance=jellyfin", "--tags", "provision", "--list-tasks"],
        cwd=ROOT / "ansible", capture_output=True, text=True,
        env={**os.environ, "ANSIBLE_NOCOLOR": "1"}, check=True,
    )
    assert "Attach the stack's declared GPU" in result.stdout
    assert "Load the app config" in result.stdout
    # No application role, registry write or published route may run during staging.
    plays = read_yaml("ansible/playbooks/apps/deploy.yml")
    for play in plays[1:]:
        assert "provision" not in play.get("tags", [])
    assert "Record the app on its stack host" not in result.stdout
    assert "Bootstrap guest" not in result.stdout


def main():
    test_effective_stack_resolution()
    test_deploy_and_recovery_paths_share_the_resolver()
    test_removal_keeps_a_gpu_used_by_sibling_apps()
    test_provision_tag_stops_before_app_deployment()
    print("GPU stack resolution, config propagation, and shared-device removal: OK")


def shared_device_repeat():
    attach = read_yaml("ansible/tasks/proxmox/attach-shared-device.yml")
    state = named(attach, "Shared device | Resolve which devices are already bound")
    missing = named(attach, "Shared device | Resolve the devices still missing")
    detach = named(read_yaml("ansible/tasks/proxmox/detach-shared-device.yml"),
                   "Detach shared device | Resolve bound state, provenance tag, and ownership per device")
    for prefix in ("path=", ""):
        context = {"_sd_config": {"stdout_lines": [
            "dev3: " + prefix + "/dev/dri/renderD128,gid=44,mode=0660",
            "dev7: " + prefix + "/dev/other",
        ]}, "shared_device_list": [{"host": "/dev/dri/renderD128"}]}
        for key, expr in state["vars"].items():
            context[key] = render(expr, **context)
        for key, expr in state["ansible.builtin.set_fact"].items():
            context[key] = render(expr, **context)
        assert context["_sd_existing_hosts"] == ["/dev/dri/renderD128", "/dev/other"]
        assert context["_sd_used_indices"] == [3, 7]
        assert render(missing["ansible.builtin.set_fact"]["_sd_missing"], **context) == []
        for tagged in (True, False):
            ctx = {"_dsd_config": context["_sd_config"],
                   "item": {"host": "/dev/dri/renderD128"},
                   "_dsd_tags": ["_.dev+dev-dri-renderd128"] if tagged else []}
            for key, expr in detach["vars"].items():
                if key != "_dsd_status":
                    ctx[key] = render(expr, **ctx)
            assert ctx["_dsd_index"] == "3"
            status = render(detach["vars"]["_dsd_status"], **ctx)
            assert status["owned"] == ("yes" if tagged else "no")
    print("Shared-device repeats recognize PVE path= and preserve unowned bindings: OK")


if __name__ == "__main__":
    shared_device_repeat()
    main()
