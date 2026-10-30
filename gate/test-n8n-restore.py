#!/usr/bin/env python3
"""Check n8n's Docker restore-swap inputs and its encryption-key hooks.

gate/test-restore-swap.py proves the swap, the database dump, load and rollback. This test
proves what n8n adds: the replaced hook gives the target the source key in Vaultwarden and
in n8n.env, and rollback gives a target its own key back once that happened.
"""

import json
import tempfile
from pathlib import Path

from restore_swap_inputs import repo, run_hook, swap_inputs

role = repo / "ansible/roles/n8n"


def run(hook, root, **extra):
    log = root / "vault.log"

    def stub(tasks):
        for task in tasks:
            for inner in [task, *task.get("block", [])]:
                inner.pop("no_log", None)
                if str(inner.get("ansible.builtin.include_tasks", "")).endswith("bitwarden/upsert-item.yml"):
                    del inner["ansible.builtin.include_tasks"]
                    inner["ansible.builtin.shell"] = (
                        "echo {{ vault_item_fields | to_json | quote }} >> " + str(log))
                if "ansible.builtin.template" in inner:
                    inner["ansible.builtin.template"].update(
                        src=str(role / "templates/n8n.env.j2"), owner=None, group=None)
        return tasks

    log.unlink(missing_ok=True)
    (root / "n8n.env").unlink(missing_ok=True)
    result = run_hook("n8n", hook, {
        "instance": "n8n-copy", "ansible_managed": "fixture",
        "n8n_env_file": str(root / "n8n.env"),
        "restore_source_encryption_key": "source-key",
        "_n8n_restore_target_encryption_key": "target-key",
        "homelabinfra_infra": {"domain": "example.invalid"},
        "app_config": {"routing": {"subdomain": "flows"}, "app": {
            "database_host": "db.example.invalid", "database_port": 5432,
            "database": {"name": "n8n_copy"}, "database_user": "n8n_copy",
            "database_password": "test-only", "port": 5678}},
        **extra,
    }, stub)
    assert result.returncode == 0, result.stdout + result.stderr
    vault = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    env = (root / "n8n.env").read_text() if (root / "n8n.env").exists() else ""
    return vault, env


def main():
    swap = swap_inputs("n8n", "restore-docker.yml")
    assert swap["recovery_swap_archives"] == ["database.pxar", "data.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ _n8n_restore_data_path }}", "source": "data",
        "owner": 1000, "group": 1000, "mode": "0750"}]
    assert swap["recovery_swap_database"] == {
        "config": "{{ app_config.app.database }}", "dump": "database/n8n.dump"}
    assert swap["recovery_swap_stop_services"] == ["n8n"]
    assert swap["recovery_swap_compose_project"] == "{{ n8n_project_dir }}"
    assert swap["recovery_swap_health_url"].endswith("/healthz")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        vault, env = run("replaced", root)
        assert vault == [{"encryption_key": "source-key"}], vault
        assert "N8N_ENCRYPTION_KEY=source-key\n" in env
        # One hostname, as Deploy renders it.
        assert "N8N_HOST=flows.example.invalid\n" in env, env
        assert "WEBHOOK_URL=https://flows.example.invalid/\n" in env, env

        vault, env = run("rollback", root, _n8n_restore_key_replaced=True,
                         restore_destination="new")
        assert vault == [{"encryption_key": "target-key"}], vault
        assert "N8N_ENCRYPTION_KEY=target-key\n" in env
        # An existing target's key is the source's own; only n8n.env is rendered again.
        vault, env = run("rollback", root, _n8n_restore_key_replaced=True,
                         restore_destination="existing")
        assert vault == [] and "N8N_ENCRYPTION_KEY=target-key\n" in env
        # A failure before the replaced hook leaves the key and n8n.env alone.
        vault, env = run("rollback", root, restore_destination="new")
        assert vault == [] and env == ""
    print("n8n: restore swap inputs and encryption-key hooks passed")


if __name__ == "__main__":
    main()
