#!/usr/bin/env python3
"""Synthetic Emby deployment, identity, media-boundary and recovery contract checks."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def parse(path):
    return yaml.safe_load(read(path))


def require(path, fragment):
    if fragment not in read(path):
        raise AssertionError(f"{path} is missing {fragment!r}")


def main():
    defaults = parse("ansible/vars/app-defaults/emby.yml")["emby_defaults"]
    assert defaults["stack"] == "media"
    assert defaults["app"]["image"] == "lscr.io/linuxserver/emby:latest"
    assert defaults["app"]["port"] == 8097
    assert defaults["app"]["container_port"] == 8096
    assert "media_kind" not in defaults["app"]
    assert defaults["routing"]["identity"] == "catalog"
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"] == {
        "backup_playbook": "backup-app.yml",
        "restore_playbook": "restore-app.yml",
    }
    assert defaults["backup"]["enabled"] and defaults["backup"]["application_consistent"]

    catalog = parse("catalog/applications.yml")["applications"]["emby"]
    assert catalog["name"] == "Emby"
    assert catalog["job"] == "deploy-emby.yaml"
    assert catalog["type"] == "Media Servers"
    assert catalog["actions"] == ["backup", "configure", "remove", "restart", "restore", "rollback", "tail"]
    recovery = parse("catalog/recovery.yml")["products"]["emby"]
    assert recovery["recovery_issue"] == 253

    compose_template = Environment(undefined=StrictUndefined).from_string(
        read("ansible/roles/emby/templates/docker-compose.yml.j2")
    )
    compose = compose_template.render(
        instance="emby-fixture",
        app_config={
            "app": {
                "image": "lscr.io/linuxserver/emby:latest",
                "config_path": "/opt/emby-fixture/config",
                "port": 18096,
                "container_port": 8096,
                "puid": 1313,
                "pgid": 1313,
                "devices": [],
            }
        },
        homelabinfra_config={"timezone": "UTC"},
        _emby_mounts=[
            {"path": "/srv/fixtures/media"},
            {"path": "/srv/fixtures/music"},
        ],
    )
    assert '"/opt/emby-fixture/config:/config"' in compose
    assert '"/srv/fixtures/media:/srv/fixtures/media:ro"' in compose
    assert '"/srv/fixtures/music:/srv/fixtures/music:ro"' in compose
    assert '"18096:8096"' in compose
    assert ":/config:ro" not in compose

    role_tasks = yaml.safe_load(read("ansible/roles/emby/tasks/main.yml"))
    mount_guard = next(
        task["ansible.builtin.assert"]["that"]
        for task in role_tasks
        if task.get("name") == "Assert each Emby library is inside a declared media mount"
    )
    guard_template = Environment(undefined=StrictUndefined).from_string(mount_guard)
    mounted = guard_template.render(
        item={"_root": "/srv/fixtures/media", "subpath": "movies"},
        _emby_mounts=[{"path": "/srv/fixtures/media"}],
    )
    unmounted = guard_template.render(
        item={"_root": "/srv/fixtures/media", "subpath": "movies"},
        _emby_mounts=[{"path": "/srv/fixtures/other"}],
    )
    assert mounted.strip().lower() == "true"
    assert unmounted.strip().lower() == "false"

    for fragment in (
        "/Users/AuthenticateByName",
        "X-Emby-Authorization",
        "homelab-infra/media/{{ instance }}",
        "/Users/Query",
        "Assert Emby requires its own authentication",
        "/Library/VirtualFolders/Query",
        "RefreshLibrary: false",
    ):
        require("ansible/roles/emby/tasks/main.yml", fragment)
    require("ansible/roles/emby/tasks/setup-wizard.yml", "/Startup/User")
    require("ansible/roles/emby/tasks/setup-wizard.yml", "/Users/{{ _emby_bootstrap_auth.json.User.Id }}/Password")
    require("ansible/roles/emby/tasks/setup-wizard.yml", "EnableAutomaticPortMapping: false")
    require("ansible/roles/emby/tasks/backup.yml", '"{{ app_config.app.config_path }}:/source:ro"')
    require("ansible/roles/emby/tasks/backup.yml", 'services: ["{{ instance }}"]')
    require("ansible/roles/emby/tasks/restore.yml", "app_config.app.config_path")
    require("ansible/roles/emby/tasks/restore.yml", "restore_source_emby_password")
    require("ansible/roles/emby/tasks/restore.yml", "_application_restore_complete: true")
    require("ansible/roles/emby/tasks/restore.yml", "Stop the named service after a failed restore")
    require("ansible/playbooks/maintenance/restore-app.yml", "restore_source_emby_user")
    require("ansible/roles/emby/tasks/restore.yml", "/Library/VirtualFolders/Query")

    playbook = read("ansible/playbooks/apps/emby.yml")
    for fragment in (
        "stack/find-or-create-host.yml",
        "attach-host-mounts.yml",
        "name: emby",
        "tasks/wiring/authentik.yml",
        "tasks/wiring/uptime-kuma.yml",
        "tasks/wiring/{{ homelabinfra_infra.dns.provider }}.yml",
        "identity | default('catalog')",
        "/emby/System/Info/Public",
    ):
        assert fragment in playbook, f"Emby playbook is missing {fragment!r}"

    job = parse("rundeck/jobs/deploy-emby.yaml")[0]
    assert job["name"] == "Deploy Emby"
    assert "playbooks/apps/emby.yml" in job["sequence"]["commands"][0]["script"]
    assert "identity: catalog" in read("config.example/apps/emby.example.yml")
    meta = read("docs/meta/134-emby/README.md")
    for fragment in ("#253", "emby-acceptance", "stack-media-acceptance", "no real media path is approved"):
        assert fragment in meta, f"Deferred live acceptance record is missing {fragment!r}"

    print("Emby catalog, app-owned auth, read-only fixture mounts, PBS scope, wiring and deferred live plan: OK")


if __name__ == "__main__":
    main()
