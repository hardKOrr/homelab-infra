#!/usr/bin/env python3
"""Exercise shared Servarr dispatch, restore guards and archived key continuity.

Checks: shared four-app dispatch, restore preview, the restore swap inputs and API key
continuity: the staged hook refuses a point without the database or key, the replaced hook
adopts the archived key in Compose, and the rollback hook puts the original Compose back.
"""

import base64
import importlib.util
import os
import subprocess
import tempfile
import threading
from http.server import HTTPServer
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("plex_test", repo / "gate/test-plex-recovery.py")
plex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plex)


def check_backup_inputs(backup):
    """The shared PBS client stops, uploads, prunes and restarts; Servarr supplies its inputs."""
    upload = next(t for t in backup if t["name"] == "Servarr backup | Quiesce Servarr and archive its data to PBS")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "docker"
    assert upload["vars"]["pbs_client_compose_project"] == "{{ servarr_project_dir }}"
    assert upload["vars"]["pbs_client_stop_services"] == ["{{ instance }}"]
    assert upload["vars"]["pbs_client_quiesced_hook"] == "{{ role_path }}/tasks/backup-quiesced.yml"
    assert upload["vars"]["pbs_client_backup_args"] == [
        "--exclude", "/logs", "--exclude", "/logs.db", "--exclude", "/logs.db-*", "--exclude", "/Backups"]
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ app_config.app.config_path }}"}]


def check_swap_inputs():
    """Servarr supplies its paths and hooks; gate/test-restore-swap.py proves the swap."""
    swap = swap_inputs("servarr")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.config_path }}", "owner": "{{ app_config.app.puid }}",
                    "group": "{{ app_config.app.pgid }}", "mode": "0750"}
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
    assert swap["recovery_swap_compose_project"] == "{{ servarr_project_dir }}"
    for hook in ["staged", "replaced", "verified", "rollback"]:
        assert swap[f"recovery_swap_{hook}_hook"] == f"{{{{ role_path }}}}/tasks/restore-{hook}.yml"


def main():
    catalog = yaml.safe_load((repo / "catalog/applications.yml").read_text())["applications"]
    backup = yaml.safe_load((repo / "ansible/roles/servarr/tasks/backup.yml").read_text())
    check_backup_inputs(backup)
    check_swap_inputs()
    staged = yaml.safe_load((repo / "ansible/roles/servarr/tasks/restore-staged.yml").read_text())
    replaced = yaml.safe_load((repo / "ansible/roles/servarr/tasks/restore-replaced.yml").read_text())
    rollback = yaml.safe_load((repo / "ansible/roles/servarr/tasks/restore-rollback.yml").read_text())
    # The rollback owner is root; the fixture keeps the production content and destination.
    for task in rollback:
        for key in ["owner", "group"]:
            task["ansible.builtin.copy"].pop(key)
    hook = yaml.safe_load((repo / "ansible/roles/servarr/tasks/backup-quiesced.yml").read_text())
    persist = next(t for t in hook if "Persist the verified" in t["name"])
    key_tasks = [t for t in staged if any(n in t["name"] for n in
                 ["Read the restored config", "Resolve the restored API", "Require the archived API",
                  "Read the Compose key", "Resolve the Compose override", "Require both Compose key"])
                 ] + replaced
    server = HTTPServer(("127.0.0.1", 0), plex.Certificate)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for app in ["prowlarr", "sonarr", "radarr", "lidarr"]:
            for instance in [app, app + "-copy"]:
                plex.dispatch.check("backup-app.yml", "Publish the application backup dispatch across plays",
                                    {"instance": instance, "app": app}, {"_ba_app": app})
                plex.dispatch.check("restore-app.yml", "Publish the application restore dispatch across plays",
                                    {"instance": instance, "app": app, "snapshot": f"host/{app}/fixture"},
                                    {"_ra_app": app, "_ra_target": instance, "_ra_overwrite": False,
                                     "_ra_recovery_point": f"host/{app}/fixture"})
            config = yaml.safe_load((repo / f"ansible/vars/app-defaults/{app}.yml").read_text())[f"{app}_defaults"]
            assert {"backup", "restore"} <= set(catalog[app]["extra"])
            assert "migrate" in catalog[app]["extra"]
            with tempfile.TemporaryDirectory() as directory:
                config["app"]["config_path"] = directory
                config_path = Path(directory) / "config.xml"
                config_path.write_text("<Config><Port>1234</Port></Config>")
                compose = Path(directory) / "docker-compose.yml"
                prefix = app.upper()
                fresh = (f"services:\n  fixture:\n    environment:\n"
                         f"      - {prefix}__APIKEY=fresh-key\n"
                         f"      - {prefix}__AUTH__APIKEY=fresh-key\n"
                         "    volumes:\n      - /mnt/media:/mnt/media\n")
                compose.write_text(fresh)
                play_vars = {
                    "instance": app, "app_config": config, "servarr_project_dir": directory,
                    "homelabinfra_infra": {"backups": {
                        "host": f"http://127.0.0.1:{server.server_port}",
                        "datastore": "fixture", "api_token_id": "fixture", "api_token_secret": "fixture"}},
                    "restore_overwrite": False, "restore_snapshot": f"host/{app}/fixture",
                    "recovery_app_config": config, "recovery_app": app,
                    "recovery_instance": app, "recovery_operation": "restore",
                }
                # The hooks read the point from <stage>/data; here that is the config directory.
                stage = Path(directory) / "stage"
                stage.mkdir()
                (stage / "data").symlink_to(directory)
                at_stage = {"recovery_swap_stage": str(stage)}
                # The preview runs the shared PBS client in a container; a fake docker
                # stands in for it. gate/test-pbs-client.py proves the client run itself.
                fake_bin = Path(directory) / "bin"
                fake_bin.mkdir()
                (fake_bin / "docker").write_text("#!/bin/sh\nexit 0\n")
                (fake_bin / "docker").chmod(0o700)
                env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                                    "ANSIBLE_ROLES_PATH": str(repo / "ansible/roles"),
                                    "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}

                def run(tasks, overrides=None, failure=None):
                    path = Path(directory) / "test.yml"
                    path.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False,
                        "vars": play_vars | (overrides or {}), "tasks": tasks}]))
                    result = subprocess.run([str(plex.dispatch.ansible), "-i", "localhost,", "-c", "local", str(path)],
                                            env=env, text=True, capture_output=True, check=False)
                    output = result.stdout + result.stderr
                    if failure:
                        assert result.returncode != 0 and failure in output, output
                    else:
                        assert result.returncode == 0, output

                resolve = {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/recovery/resolve-method.yml")}
                # Use the production Docker dispatch name expression, with the same localhost facts.
                for operation, short in [("backup", "ba"), ("restore", "ra")]:
                    source = yaml.safe_load((repo / f"ansible/playbooks/maintenance/{operation}-app.yml").read_text())
                    docker_play = next(p for p in source if str(p["hosts"]).startswith("apptarget_"))
                    dispatch_task = next(t for t in docker_play["tasks"] if "ansible.builtin.include_role" in t)
                    role_name = dispatch_task["ansible.builtin.include_role"]["name"]
                    run([resolve, {"ansible.builtin.set_fact": {f"_{short}_app": app}},
                         {"ansible.builtin.assert": {"that": [
                             f"{role_name[3:-3]} == 'servarr'", "recovery_method_resolved == 'native'"]}}])
                preview = {"ansible.builtin.include_role": {"name": "servarr", "tasks_from": "restore"}}
                run([resolve, preview, {"ansible.builtin.assert": {"that": [
                    "not (_application_restore_complete | default(false))", "recovery_swap_stage is not defined"]}}])
                assert config_path.read_text() == "<Config><Port>1234</Port></Config>"
                assert compose.read_text() == fresh
                run([preview], {"restore_overwrite": True, "restore_snapshot": "host/other/fixture"},
                    f"must belong to host/{app}/")
                checks = [t for t in staged if any(n in t["name"] for n in
                          ["Check the staged recovery", "Refuse an incomplete archive"])]
                run(checks, at_stage, "lacks the Servarr database")
                (Path(directory) / f"{app}.db").write_text("fixture database")
                run(checks, at_stage)
                run([persist], {"_servarr_backup_key": "archived-key"})
                assert "<ApiKey>archived-key</ApiKey>" in config_path.read_text()
                run(key_tasks, at_stage)
                assert compose.read_text().count("=archived-key") == 2
                assert "/mnt/media:/mnt/media" in compose.read_text()
                # An unchanged key is valid; missing or duplicate overrides fail before replacement.
                run(key_tasks, at_stage)
                compose.write_text(fresh.replace(f"      - {prefix}__APIKEY=fresh-key\n", ""))
                run(key_tasks, at_stage, failure="Compose must contain exactly")
                compose.write_text(fresh.replace(f"{prefix}__AUTH__APIKEY", f"{prefix}__APIKEY"))
                run(key_tasks, at_stage, failure="Compose must contain exactly")
                compose.write_text(fresh)
                # A rollback after the key was adopted puts the original overrides back.
                run(key_tasks + rollback, at_stage)
                assert compose.read_text() == fresh
                # Final Deploy's production key resolution must prefer the archive to a fresh vault key.
                main_tasks = yaml.safe_load((repo / "ansible/roles/servarr/tasks/main.yml").read_text())
                resolve_key = next(t for t in main_tasks if t["name"] == "Resolve the API key")
                run([resolve_key, {"ansible.builtin.assert": {"that": ["_servarr_api_key == 'archived-key'"]}}],
                    {"_servarr_config_existing": {"content": base64.b64encode(config_path.read_bytes()).decode()},
                     "homelabinfra_vault": {"media": {app: {"api_key": "fresh-key"}}}})
                config_path.write_text("<Config/>")
                run(key_tasks, at_stage, failure="lacks ApiKey")
    finally:
        server.shutdown()
        server.server_close()
    print("Servarr recovery: backup and swap inputs, Compose rollback; four-app shared dispatch, preview, group guards and key continuity passed")


if __name__ == "__main__":
    main()
