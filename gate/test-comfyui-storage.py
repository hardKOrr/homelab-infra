#!/usr/bin/env python3
"""ComfyUI runs unprivileged, so every directory Compose mounts must belong to that user.

The ai-dock image runs ComfyUI as uid 1000, gid 1111. Root-owned 0750 mounts left it unable
to save anything; a restore that copies the archive back as root must hand them back too.

Checks: every directory ComfyUI's Compose file mounts belongs to its runtime user after
deploy, and the restore swap hands them back through the same task file.
"""

from pathlib import Path
import re
import os
import stat
import subprocess
import tempfile

import yaml

from restore_swap_inputs import swap_inputs


ROOT = Path(__file__).resolve().parent.parent
ROLE = ROOT / "ansible/roles/comfyui"


def check_native_modes():
    # Exercise the actual task: atomic uploads and ordinary settings files keep
    # their modes, while the Docker runtime still grants its required group read.
    with tempfile.TemporaryDirectory(prefix="comfyui-storage-") as tmp:
        root = Path(tmp)
        paths = {key: str(root / key) for key in
                 ("models_path", "config_path", "custom_nodes_path", "input_path", "output_path")}
        for path in paths.values():
            Path(path).mkdir(mode=0o750)
        workflow = Path(paths["config_path"]) / "workflow.json"
        settings = Path(paths["config_path"]) / "settings.json"
        workflow.write_text("{}")
        settings.write_text("{}")
        workflow.chmod(0o600)
        settings.chmod(0o640)
        play = root / "play.yml"
        base = {"hosts": "localhost", "gather_facts": False,
                "vars": {"comfyui_owner_uid": os.getuid(), "comfyui_owner_gid": os.getgid()},
                "tasks": [{"ansible.builtin.import_tasks": str(ROLE / "tasks/storage-ownership.yml")}]}
        for method in ("native", "native", "docker"):
            base["vars"]["app_config"] = {"hosting": {"method": method}, "app": paths}
            play.write_text(yaml.safe_dump([base]))
            result = subprocess.run(
                [os.environ.get("GATE_ANSIBLE_PLAYBOOK", str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook")),
                 "-i", "localhost,", "-c", "local", str(play)],
                capture_output=True, text=True, timeout=120)
            assert result.returncode == 0, result.stdout + result.stderr
            if method == "native":
                assert stat.S_IMODE(workflow.stat().st_mode) == 0o600
                assert stat.S_IMODE(settings.stat().st_mode) == 0o640
                assert re.search(r"changed=0\b", result.stdout), result.stdout
            else:
                assert stat.S_IMODE(workflow.stat().st_mode) == 0o640


def main():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/comfyui.yml").read_text()
    )["comfyui_defaults"]["app"]
    assert (defaults["run_uid"], defaults["run_gid"]) == (1000, 1111)

    compose = (ROLE / "templates/docker-compose.yml.j2").read_text()
    mounted = set(re.findall(r'"\{\{ app_config\.app\.(\w+_path) \}\}:', compose))
    assert mounted, "no app_config.app.*_path mounts found in the Compose template"

    ownership = yaml.safe_load((ROLE / "tasks/storage-ownership.yml").read_text())[0]
    file_args = ownership["ansible.builtin.file"]
    assert file_args["owner"] == "{{ comfyui_owner_uid }}"
    assert file_args["group"] == "{{ comfyui_owner_gid }}"
    assert file_args["recurse"] is True
    assert "x" not in file_args["mode"].split(",")[0], "files must not become executable"
    owned = {re.search(r"app\.(\w+_path)", item).group(1) for item in ownership["loop"]}
    assert owned == mounted, f"mounted {sorted(mounted)} but owned {sorted(owned)}"

    assert "include_tasks: storage-ownership.yml" in (ROLE / "tasks/docker.yml").read_text()
    # The restore swap installs the tree as root, then hands the mounts back before the start.
    swap = swap_inputs("comfyui", "restore-guest.yml")
    assert swap["recovery_swap_replaced_hook"] == "{{ role_path }}/tasks/storage-ownership.yml"
    assert [(p["owner"], p["group"]) for p in swap["recovery_swap_paths"]] == [
        ("{{ 0 if app_config.hosting.method == 'docker' else comfyui_owner_uid }}",
         "{{ 0 if app_config.hosting.method == 'docker' else comfyui_owner_gid }}")]

    check_native_modes()

    print("ComfyUI storage: every mounted directory belongs to the runtime user after deploy and restore")


if __name__ == "__main__":
    main()
