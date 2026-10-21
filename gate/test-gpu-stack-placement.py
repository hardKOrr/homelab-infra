#!/usr/bin/env python3
"""GPU-backed stack apps resolve to their device owner's shared VM."""

from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


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
        "ollama-gpu": {},
    }
    assert effective_stack(
        {"stack": "photos", "app": {"gpu_device": "intel-igpu"}}, devices
    ) == "media"
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
        ("ansible/playbooks/apps/remove.yml", "../../tasks/stack/resolve-app-stack.yml"),
        ("ansible/playbooks/maintenance/restore-app.yml", "../../tasks/stack/resolve-app-stack.yml"),
    ):
        assert needle in (ROOT / path).read_text(encoding="utf-8"), path


def test_removal_keeps_a_gpu_used_by_sibling_apps():
    remove = read_yaml("ansible/playbooks/apps/remove.yml")
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

    tasks = remove
    detach_immich = named(tasks, "Detach Immich's project-owned PCI iGPU")
    detach_frigate = named(tasks, "Detach Frigate's project-owned PCI iGPU")
    for task in (detach_immich, detach_frigate):
        conditions = task["when"] if isinstance(task["when"], list) else [task["when"]]
        assert any("_rm_stack_has_other_apps" in condition for condition in conditions)


def main():
    test_effective_stack_resolution()
    test_deploy_and_recovery_paths_share_the_resolver()
    test_removal_keeps_a_gpu_used_by_sibling_apps()
    print("GPU stack resolution, config propagation, and shared-device removal: OK")


if __name__ == "__main__":
    main()
