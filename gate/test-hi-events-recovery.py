#!/usr/bin/env python3
"""Keep Hi.Events' database and storage in one safe native recovery unit."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    defaults = (ROOT / "ansible/vars/app-defaults/hi-events.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "ansible/roles/hi-events/tasks/main.yml").read_text(encoding="utf-8")
    restore = (ROOT / "ansible/roles/hi-events/tasks/restore.yml").read_text(encoding="utf-8")
    remove = (ROOT / "ansible/playbooks/apps/remove.yml").read_text(encoding="utf-8")

    assert "methods: [native]" in defaults
    # Backup and restore go through the shared Kubernetes recovery pair
    # (gate/test-k8s-recovery-jobs.py renders them) with the PostgreSQL database step.
    for tasks in (deploy, restore):
        assert "tasks/database/kubernetes-recovery-step.yml" in tasks
        assert "k8s_database_step_engine: postgresql" in tasks
    assert "tasks/kubernetes/recovery-backup.yml" in deploy
    assert "tasks/kubernetes/recovery-restore.yml" in restore
    assert '- "{{ restore_target }}-redis"' in restore.split("restart:", 1)[1]

    # Remove drops only the named DB/role after validating registration, ownership,
    # and that no other canonical application item refers to either resource.
    assert "_rm_hi_events_shared_databases | length == 0" in remove
    assert "_rm_hi_events_shared_roles | length == 0" in remove
    assert "_rm_hi_events_database_item.database_name" in remove
    assert "Refuse to delete Hi.Events' database with an unexpected owner" in remove
    assert "DROP DATABASE {{ hostvars['localhost']._rm_hi_events_database_config.name }}" in remove
    assert "DROP ROLE {{ hostvars['localhost']._rm_hi_events_database_config.role }}" in remove

    print("Hi.Events backs up, restores, and removes only its named database plus storage")


if __name__ == "__main__":
    main()
