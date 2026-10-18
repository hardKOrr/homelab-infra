#!/usr/bin/env python3
"""Keep Hi.Events' database and storage in one safe native recovery unit."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    defaults = (ROOT / "ansible/vars/app-defaults/hi-events.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "ansible/roles/hi-events/tasks/main.yml").read_text(encoding="utf-8")
    backup = (ROOT / "ansible/roles/hi-events/templates/backup-cronjob.yaml.j2").read_text(encoding="utf-8")
    restore = (ROOT / "ansible/roles/hi-events/tasks/restore.yml").read_text(encoding="utf-8")
    restore_job = (ROOT / "ansible/roles/hi-events/templates/restore-job.yaml.j2").read_text(encoding="utf-8")
    remove = (ROOT / "ansible/playbooks/apps/remove.yml").read_text(encoding="utf-8")

    assert "methods: [native]" in defaults
    assert "_he_pg_client_version" in deploy
    assert "'db-password': app_config.app.database_password" in deploy
    assert "pg_dump --format=custom --no-owner --no-privileges" in backup
    assert "database.pxar:/backup/database" in backup
    assert "storage.pxar:/app-storage" in backup
    assert "secretKeyRef: { name: {{ instance }}-backup, key: db-password }" in backup
    assert 'restore "${SNAPSHOT}" database.pxar /restore/database' in restore_job
    assert 'restore "${SNAPSHOT}" storage.pxar /app-storage' in restore_job
    assert "pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error" in restore_job
    assert "Restart the disposable Redis cache" in restore
    assert "_he_r_database_password" in restore
    assert "_he_r_pg_client_version" in restore

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
