#!/usr/bin/env python3
"""ComfyUI runs unprivileged, so every directory Compose mounts must belong to that user.

The ai-dock image runs ComfyUI as uid 1000, gid 1111. Root-owned 0750 mounts left it unable
to save anything; a restore that copies the archive back as root must hand them back too.
"""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parent.parent
ROLE = ROOT / "ansible/roles/comfyui"


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
    assert file_args["owner"] == "{{ app_config.app.run_uid }}"
    assert file_args["group"] == "{{ app_config.app.run_gid }}"
    assert file_args["recurse"] is True
    assert "x" not in file_args["mode"].split(",")[0], "files must not become executable"
    owned = {re.search(r"app\.(\w+_path)", item).group(1) for item in ownership["loop"]}
    assert owned == mounted, f"mounted {sorted(mounted)} but owned {sorted(owned)}"

    for task_file in ("main.yml", "restore.yml"):
        text = (ROLE / "tasks" / task_file).read_text()
        assert "include_tasks: storage-ownership.yml" in text, task_file
    restore = (ROLE / "tasks/restore.yml").read_text()
    assert restore.index("storage-ownership.yml") > restore.index("Copy archived models")
    assert restore.index("storage-ownership.yml") < restore.index("ComfyUI restore | Start ComfyUI")

    print("ComfyUI storage: every mounted directory belongs to the runtime user after deploy and restore")


if __name__ == "__main__":
    main()
