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
    assert "proxmox-backup-client restore \"$SNAPSHOT\" data.pxar /pre-restore >\"$pbs_error_log\" 2>&1" in script
    for exit_code in [82, 85, 86, 87, 88, 89]:
        assert f"exit {exit_code}" in script
    for pattern in [
        "authentication|permission denied|unauthorized|unauthorised|forbidden|401|403",
        "connection|timed out|timeout|no route|resolve|tls|certificate|ssl",
        "not found|no such file|does not exist|unknown archive|missing archive",
        "decrypt|encryption|wrong key|key.*invalid",
        "checksum|digest|corrupt|chunk",
    ]:
        assert pattern in script
    assert "test -s /pre-restore/webui.db || exit 83" in script
    assert "PRAGMA integrity_check;\")\" = ok || exit 84" in script

    classify = task_named(
        block, "Open WebUI restore | Classify the existing-target B archive error"
    )
    assert classify["changed_when"] is False
    assert classify["no_log"] is True
    assert classify["when"] == "(_open_webui_restore_b_validation.rc | default(1) | int) in [82, 85, 86, 87, 88, 89]"
    classification = classify["ansible.builtin.set_fact"][
        "_open_webui_restore_b_failure_class"
    ]
    for reason in [
        "PBS authorization or credentials",
        "PBS transport or TLS",
        "snapshot archive is missing",
        "archive encryption key error",
        "archive chunk or integrity error",
        "unclassified PBS archive extraction error",
    ]:
        assert reason in classification

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
    assert "cause=" in message
    assert "_open_webui_restore_b_validation.stderr" not in message
    assert "_open_webui_restore_b_validation.stdout" not in message


if __name__ == "__main__":
    open_webui_restore_prepoint_diagnostics()
    print("ok Open WebUI restore prepoint diagnostics")
