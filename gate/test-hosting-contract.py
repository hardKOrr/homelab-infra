#!/usr/bin/env python3
"""Check app hosting declarations, rendered defaults, and preflight refusal."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

import yaml


ROOT = Path(__file__).resolve().parent.parent
APP_DEFAULTS = ROOT / "ansible/vars/app-defaults"
APP_PLAYBOOKS = ROOT / "ansible/playbooks/apps"
STACK_DEFAULTS = yaml.safe_load((ROOT / "ansible/vars/stack-defaults.yml").read_text())[
    "stack_defaults"
]
COMBINATIONS = {"docker-lxc", "docker-vm", "kubernetes", "native-lxc", "native-vm"}
METHODS = {"docker", "kubernetes", "native"}
ANSIBLE_PLAYBOOK = os.environ.get("GATE_ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")


def defaults_for(path):
    document = yaml.safe_load(path.read_text()) or {}
    assert len(document) == 1, f"{path}: expected one top-level defaults block"
    return next(iter(document.values()))


def app_source(slug):
    playbook = APP_PLAYBOOKS / f"{slug}.yml"
    assert playbook.is_file(), f"{slug}: missing deploy playbook {playbook}"
    chunks = [playbook.read_text()]
    for role_name in {slug, slug.replace("-", "_")}:
        role_tasks = ROOT / "ansible/roles" / role_name / "tasks"
        if role_tasks.is_dir():
            chunks.extend(path.read_text() for path in role_tasks.rglob("*.yml"))
    return playbook, "\n".join(chunks)


def current_combination(slug, defaults):
    """Infer today's path from deployment code and guest shape, not hosting.supported."""
    playbook, source = app_source(slug)
    if defaults.get("stack"):
        method = "docker"
        stack = defaults["stack"]
        stack_config = STACK_DEFAULTS.get(stack, {})
        guest_type = (
            stack_config.get("guest_type")
            or STACK_DEFAULTS.get("_default", {}).get("guest_type")
            or "vm"
        )
    elif "tasks/kubernetes/open-app-access.yml" in source or "kubernetes.core." in source:
        method = "kubernetes"
        guest_type = ""
    elif re.search(r"community\.docker\.|docker_compose_v2|docker_container", source):
        method = "docker"
        proxmox = defaults.get("proxmox", {})
        guest_type = (
            proxmox.get("guest_type")
            or proxmox.get("type")
            or ("vm" if isinstance(proxmox.get("vm"), dict) else "lxc" if isinstance(proxmox.get("lxc"), dict) else "vm")
        )
    else:
        method = "native"
        proxmox = defaults.get("proxmox", {})
        guest_type = (
            proxmox.get("guest_type")
            or proxmox.get("type")
            or ("vm" if isinstance(proxmox.get("vm"), dict) else "lxc" if isinstance(proxmox.get("lxc"), dict) else "vm")
        )
    return "kubernetes" if method == "kubernetes" else f"{method}-{guest_type}"


def validate_deploy_preflight(slug):
    playbook, _ = app_source(slug)
    plays = yaml.safe_load(playbook.read_text())
    assert isinstance(plays, list), f"{playbook}: expected a play list"
    for play in plays:
        pre_tasks = play.get("pre_tasks") or []
        guard_indices = [
            index
            for index, task in enumerate(pre_tasks)
            if task.get("ansible.builtin.include_tasks")
            == "../../tasks/hosting/validate-app.yml"
        ]
        if not guard_indices:
            continue
        guard_index = guard_indices[0]
        assert len(guard_indices) == 1, f"{playbook}: hosting preflight appears more than once"
        merged_indices = [
            index
            for index, task in enumerate(pre_tasks)
            if "ansible.builtin.set_fact" in task
            and any(
                key in task["ansible.builtin.set_fact"]
                for key in ("app_config", "k8s_cluster_config", "pdm_config")
            )
        ]
        prior_merges = [index for index in merged_indices if index < guard_index]
        assert prior_merges, f"{playbook}: no merged app config before hosting preflight"
        assert guard_index == prior_merges[-1] + 1, (
            f"{playbook}: hosting preflight must immediately follow merged app config"
        )
        assert play.get("tasks") is not None, f"{playbook}: deploy play has no tasks section"
        return
    raise AssertionError(f"{playbook}: hosting preflight is not in the app's pre_tasks")


def check_declarations(paths):
    expected_by_slug = {}
    for path in paths:
        slug = path.stem
        defaults = defaults_for(path)
        hosting = defaults.get("hosting")
        assert isinstance(hosting, dict), f"{path}: missing hosting mapping"
        method = hosting.get("method")
        supported = hosting.get("supported")
        impossible = hosting.get("impossible")
        assert method in METHODS, f"{path}: invalid hosting.method {method!r}"
        assert isinstance(supported, list) and supported, f"{path}: hosting.supported must be nonempty"
        assert isinstance(impossible, dict), f"{path}: hosting.impossible must be a mapping"
        assert set(supported) <= COMBINATIONS, f"{path}: unknown supported combination"
        assert set(impossible) <= COMBINATIONS, f"{path}: unknown impossible combination"
        assert not (set(supported) & set(impossible)), f"{path}: supported and impossible overlap"
        for combination, reason in impossible.items():
            assert isinstance(reason, str) and reason.strip(), (
                f"{path}: {combination} needs a non-empty technical reason"
            )

        expected = current_combination(slug, defaults)
        assert supported == [expected], (
            f"{path}: expected exactly today's combination [{expected}], got {supported}"
        )
        assert method == expected.split("-", 1)[0], (
            f"{path}: default method {method!r} differs from current {expected} behavior"
        )
        expected_by_slug[slug] = expected

        proxmox = defaults.get("proxmox", {})
        install = defaults.get("install", {})
        is_appliance = bool(proxmox.get("installer")) or install.get("method") == "official-iso"
        is_cluster = bool(defaults.get("cluster")) and slug == "k3s-cluster"
        if is_appliance or is_cluster:
            assert set(impossible) == COMBINATIONS - {expected}, (
                f"{path}: appliance/cluster must explain all four unsupported combinations"
            )
        else:
            assert not impossible, f"{path}: impossible combinations are empty today"

        validate_deploy_preflight(slug)
    return expected_by_slug


def run_ansible(playbook, *extra_args):
    assert ANSIBLE_PLAYBOOK, "ansible-playbook executable is not configured"
    command = [
        ANSIBLE_PLAYBOOK,
        "-i",
        str(ROOT / "gate/fixtures/localhost.ini"),
        str(ROOT / "gate/fixtures" / playbook),
        *extra_args,
    ]
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def check_rendered_defaults(expected_by_slug):
    result = run_ansible("hosting-render.yml")
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"default hosting render failed:\n{output}"
    actual = dict(re.findall(r"HOSTING_RENDERED ([A-Za-z0-9_-]+)=([a-z-]+)", output))
    assert actual == expected_by_slug, (
        f"rendered combinations differ; missing={set(expected_by_slug) - set(actual)}, "
        f"unexpected={set(actual) - set(expected_by_slug)}, "
        f"mismatched={[(slug, expected_by_slug[slug], actual.get(slug)) for slug in expected_by_slug if actual.get(slug) != expected_by_slug[slug]]}"
    )


def check_unsupported_fails_before_state_change():
    with tempfile.TemporaryDirectory(prefix="hosting-contract-") as tmp:
        marker = Path(tmp) / "must-not-be-created"
        result = run_ansible("hosting-refusal.yml", "-e", f"hosting_marker_path={marker}")
        output = result.stdout + result.stderr
        assert result.returncode != 0, "unsupported docker-lxc selection unexpectedly passed"
        assert "docker-lxc" in output, f"failure did not name docker-lxc:\n{output}"
        assert "official ISO installer boots a complete appliance OS" in output, (
            f"failure did not report the technical impossibility reason:\n{output}"
        )
        assert not marker.exists(), "a state-changing task ran after unsupported hosting was selected"


def main():
    paths = sorted(path for path in APP_DEFAULTS.glob("*.yml") if path.name != "_template.yml")
    assert paths, "no application defaults were found"
    expected_by_slug = check_declarations(paths)
    check_rendered_defaults(expected_by_slug)
    check_unsupported_fails_before_state_change()
    print(f"Hosting contract passed for {len(paths)} app defaults and preflight refusal.")


if __name__ == "__main__":
    main()
