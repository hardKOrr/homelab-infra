#!/usr/bin/env python3
"""Check Odoo's restore-swap inputs.

gate/test-restore-swap.py proves the swap, the database dump, load and rollback. This test
proves what Odoo supplies: its data_dir as the image's uid, its database and dump, and its
service and health URL.
"""

from restore_swap_inputs import swap_inputs


def main():
    swap = swap_inputs("odoo")
    assert swap["recovery_swap_archives"] == ["database.pxar", "filestore.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ _odoo_restore_filestore_path }}", "source": "filestore",
        "owner": "{{ app_config.app.puid }}", "group": "{{ app_config.app.pgid }}",
        "mode": "0750"}]
    assert swap["recovery_swap_database"] == {
        "config": "{{ app_config.app.database }}", "dump": "database/odoo.dump"}
    assert swap["recovery_swap_stop_services"] == ["odoo"]
    assert swap["recovery_swap_compose_project"] == "{{ odoo_project_dir }}"
    assert swap["recovery_swap_health_url"].endswith("/web/health")
    print("Odoo: restore swap inputs passed")


if __name__ == "__main__":
    main()
