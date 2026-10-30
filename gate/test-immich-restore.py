#!/usr/bin/env python3
"""Check Immich's restore-swap inputs.

gate/test-restore-swap.py proves the swap, the mount handling, the private database dump
and the database rollback. This test proves what Immich supplies: its media mount as the
media owner, its named database and dump, and its services and health URL.
"""

from restore_swap_inputs import swap_inputs


def main():
    swap = swap_inputs("immich")
    assert swap["recovery_swap_archives"] == ["database.pxar", "media.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ _immich_restore_media_path }}", "source": "media",
        "owner": "{{ app_config.media_storage.owner.uid | int }}",
        "group": "{{ app_config.media_storage.owner.gid | int }}",
        "mount": True}]
    assert swap["recovery_swap_database"] == {
        "config": "{{ app_config.app.database }}", "dump": "database/immich.dump"}
    assert swap["recovery_swap_stop_services"] == ["immich-server", "immich-machine-learning"]
    assert swap["recovery_swap_compose_project"] == "{{ immich_project_dir }}"
    assert swap["recovery_swap_health_url"].endswith("/api/server/ping")
    assert not any(key.endswith("_hook") for key in swap), "the swap owns Immich's database steps"
    print("Immich: restore swap inputs passed")


if __name__ == "__main__":
    main()
