#!/usr/bin/env python3
"""Check Navidrome Restore's inputs to the shared restore swap."""

from restore_swap_inputs import swap_inputs


def main():
    swap = swap_inputs("navidrome", "restore-guest.yml")
    # The stack host is an unprivileged LXC's nested Docker: the archive's modes cannot be
    # applied there, so the swap assigns the container user and the directory mode.
    assert swap["recovery_swap_ignore_permissions"] is True
    [path] = swap["recovery_swap_paths"]
    assert path["path"] == "{{ app_config.app.data_path }}"
    assert path["owner"] == "{{ app_config.app.puid | default(1313) | int }}"
    assert path["group"] == "{{ app_config.app.pgid | default(1313) | int }}"
    assert path["mode"] == "0750" and not path.get("mount", False)
    assert "app_config.hosting.method" in swap["recovery_swap_runtime"]
    assert swap["recovery_swap_stop_services"] == [
        "{{ navidrome_native_service if navidrome_native | bool else instance }}"
    ]
    assert swap["recovery_swap_health_url"].endswith("/ping")
    print("Navidrome Restore: ownership-safe swap inputs passed")


if __name__ == "__main__":
    main()
