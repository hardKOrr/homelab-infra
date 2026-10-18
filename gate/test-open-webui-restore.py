#!/usr/bin/env python3
"""Check Open WebUI restore failures remain secret-safe and stage-specific."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def open_webui_restore_prepoint_diagnostics():
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/open-webui/tasks/restore.yml").read_text(
            encoding="utf-8"
        )
    )
    replace = task_named(tasks, "Open WebUI restore | Replace application state")
    block = replace["block"]
    verify = task_named(
        block, "Open WebUI restore | Verify the independent existing-target B point"
    )
    assert verify["no_log"] is True
    assert verify["failed_when"] is False
    assert verify["register"] == "_open_webui_restore_b_validation"
    script = verify["ansible.builtin.command"]["argv"][-1]
    assert "proxmox-backup-client restore \"$SNAPSHOT\" data.pxar /pre-restore --allow-existing-dirs true || exit 82" in script
    assert "test -s /pre-restore/webui.db || exit 83" in script
    assert "PRAGMA integrity_check;\")\" = ok || exit 84" in script

    result = task_named(
        block, "Open WebUI restore | Assert the existing-target B point verified"
    )
    assert result["ansible.builtin.assert"]["that"] == [
        "_open_webui_restore_b_validation.rc | default(1) | int == 0"
    ]
    message = result["ansible.builtin.assert"]["fail_msg"]
    for reason in [
        "PBS client setup",
        "PBS archive extraction",
        "webui.db missing at archive root",
        "SQLite integrity check",
        "No target data was replaced.",
    ]:
        assert reason in message
    assert "_open_webui_restore_b_validation.stderr" not in message
    assert "_open_webui_restore_b_validation.stdout" not in message

    selected_a = task_named(block, "Open WebUI restore | Extract and validate selected A")
    selected_a_script = selected_a["ansible.builtin.command"]["argv"][-1]
    assert "proxmox-backup-client restore \"$SNAPSHOT\" data.pxar /restore --allow-existing-dirs true" in selected_a_script


if __name__ == "__main__":
    open_webui_restore_prepoint_diagnostics()
    print("ok Open WebUI restore prepoint diagnostics")
