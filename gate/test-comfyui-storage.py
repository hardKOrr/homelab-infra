#!/usr/bin/env python3
"""ComfyUI runs unprivileged, so every directory Compose mounts must belong to that user.

The ai-dock image runs ComfyUI as uid 1000, gid 1111. Root-owned 0750 mounts left it unable
to save anything; a restore that copies the archive back as root must hand them back too.

Checks: every directory ComfyUI's Compose file mounts belongs to its runtime user after
deploy, and the restore swap hands them back through the same task file.
"""

from pathlib import Path
import re

import yaml

from restore_swap_inputs import swap_inputs


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

    assert "include_tasks: storage-ownership.yml" in (ROLE / "tasks/main.yml").read_text()
    # The restore swap installs the tree as root, then hands the mounts back before the start.
    swap = swap_inputs("comfyui")
    assert swap["recovery_swap_replaced_hook"] == "{{ role_path }}/tasks/storage-ownership.yml"
    assert [(p["owner"], p["group"]) for p in swap["recovery_swap_paths"]] == [(0, 0)]

    print("ComfyUI storage: every mounted directory belongs to the runtime user after deploy and restore")


if __name__ == "__main__":
    main()
