#!/usr/bin/env python3
"""Synthetic Navidrome deployment, mount-boundary and recovery contract checks."""

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
    defaults = parse("ansible/vars/app-defaults/navidrome.yml")["navidrome_defaults"]
    assert defaults["stack"] == "media"
    assert defaults["media_storage"] == {}
    assert defaults["app"]["image"] == "deluan/navidrome:latest"
    assert defaults["app"]["port"] == 4533
    assert defaults["app"]["container_port"] == 4533
    assert defaults["app"]["library_subpath"] == "music"
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"] == {
        "backup_playbook": "backup-app.yml",
        "restore_playbook": "restore-app.yml",
    }
    assert defaults["backup"]["enabled"] and defaults["backup"]["application_consistent"]
    assert defaults["routing"]["identity"] == "catalog"

    catalog = parse("catalog/applications.yml")["applications"]["navidrome"]
    assert catalog == {
        "name": "Navidrome",
        "job": "deploy-navidrome.yaml",
        "root": "Applications",
        "category": "Media & Entertainment",
        "type": "Media Servers",
        "scope": "estate",
        "actions": ["backup", "configure", "remove", "restart", "restore", "rollback", "tail"],
    }
    assert parse("catalog/recovery.yml")["products"]["navidrome"]["recovery_issue"] == 135

    compose_template = Environment(undefined=StrictUndefined).from_string(
        read("ansible/roles/navidrome/templates/docker-compose.yml.j2")
    )
    compose = compose_template.render(
        instance="navidrome-fixture",
        app_config={
            "app": {
                "image": "deluan/navidrome:latest",
                "data_path": "/opt/navidrome-fixture/data",
                "port": 14533,
                "container_port": 4533,
                "puid": 1313,
                "pgid": 1313,
            }
        },
        homelabinfra_config={"timezone": "UTC"},
        _navidrome_music_path="/srv/fixtures/media/music",
    )
    assert 'user: "1313:1313"' in compose
    assert "ND_DATAFOLDER=/data" in compose
    assert "ND_MUSICFOLDER=/music" in compose
    assert '"/opt/navidrome-fixture/data:/data"' in compose
    assert '"/srv/fixtures/media/music:/music:ro"' in compose
    assert '"/srv/fixtures/media:/music' not in compose
    assert ":rw" not in compose
    assert '"14533:4533"' in compose

    role_tasks = yaml.safe_load(read("ansible/roles/navidrome/tasks/main.yml"))
    mount_guard = next(
        task["ansible.builtin.assert"]["that"]
        for task in role_tasks
        if task.get("name") == "Assert Navidrome music path is inside a declared media mount"
    )
    guard_template = Environment(undefined=StrictUndefined).from_string(mount_guard)
    mounted = guard_template.render(
        _navidrome_music_path="/srv/fixtures/media/music",
        _navidrome_mounts=[{"path": "/srv/fixtures/media"}],
    )
    unmounted = guard_template.render(
        _navidrome_music_path="/srv/fixtures/other/music",
        _navidrome_mounts=[{"path": "/srv/fixtures/media"}],
    )
    assert mounted.strip().lower() == "true"
    assert unmounted.strip().lower() == "false"

    for fragment in (
        "library_subpath",
        "dot-directory traversal",
        "Assert Navidrome storage owner matches its container user",
        "Verify the declared media mounts are present",
        "Assert Navidrome data stays inside its named project",
        "Verify the Navidrome music path exists",
        "_navidrome_music_path",
    ):
        require("ansible/roles/navidrome/tasks/main.yml", fragment)
    require("ansible/roles/navidrome/tasks/backup.yml", "Assert data stays inside its named project")
    require("ansible/roles/navidrome/tasks/backup.yml", '"{{ app_config.app.data_path }}:/source:ro"')
    require("ansible/roles/navidrome/tasks/backup.yml", 'services: ["{{ instance }}"]')
    require("ansible/roles/navidrome/tasks/restore.yml", "Assert data stays inside its named project")
    require("ansible/roles/navidrome/tasks/restore.yml", "data.pxar /restore")
    require("ansible/roles/navidrome/tasks/restore.yml", "_application_restore_complete: true")
    require("ansible/roles/navidrome/tasks/restore.yml", "Stop the named service after a failed restore")
    require("ansible/roles/navidrome/tasks/restore.yml", "external music library was not touched")

    playbook = read("ansible/playbooks/apps/navidrome.yml")
    for fragment in (
        "stack/find-or-create-host.yml",
        "attach-host-mounts.yml",
        "name: navidrome",
        "tasks/wiring/authentik.yml",
        "tasks/wiring/uptime-kuma.yml",
        "tasks/wiring/{{ homelabinfra_infra.dns.provider }}.yml",
        "recovery_isolated",
        "/ping",
    ):
        assert fragment in playbook, f"Navidrome playbook is missing {fragment!r}"

    job = parse("rundeck/jobs/deploy-navidrome.yaml")[0]
    assert job["name"] == "Deploy Navidrome"
    assert "playbooks/apps/navidrome.yml" in job["sequence"]["commands"][0]["script"]
    assert "read-only" in read("config.example/apps/navidrome.example.yml")
    meta = read("docs/meta/135-navidrome/README.md")
    for fragment in ("#198", "navidrome-acceptance", "stack-media-acceptance", "no real music path"):
        assert fragment in meta, f"Deferred live acceptance record is missing {fragment!r}"

    print("Navidrome catalog, scoped read-only music mount, PBS recovery, wiring and deferred live plan: OK")


if __name__ == "__main__":
    main()
