#!/usr/bin/env python3
"""Exercise shared Servarr dispatch, restore guards and archived key continuity."""

import base64
import importlib.util
import os
import subprocess
import tempfile
import threading
from http.server import HTTPServer
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("plex_test", repo / "gate/test-plex-recovery.py")
plex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plex)


def main():
    catalog = yaml.safe_load((repo / "catalog/applications.yml").read_text())["applications"]
    backup = yaml.safe_load((repo / "ansible/roles/servarr/tasks/backup.yml").read_text())
    restore = yaml.safe_load((repo / "ansible/roles/servarr/tasks/restore.yml").read_text())
    capture = next(t["block"] for t in backup if "block" in t)
    restored = next(t["block"] for t in restore if "block" in t)
    persist = next(t for t in capture if "Persist the verified" in t["name"])
    key_tasks = [t for t in restored if any(n in t["name"] for n in
                 ["Read the restored config", "Resolve the restored API", "Require the archived API",
                  "Adopt the archived key"]) ]
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
                env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                                    "ANSIBLE_ROLES_PATH": str(repo / "ansible/roles")}

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
                    "not (_application_restore_complete | default(false))", "_servarr_restore_tmp is skipped"]}}])
                assert config_path.read_text() == "<Config><Port>1234</Port></Config>"
                assert compose.read_text() == fresh
                run([preview], {"restore_overwrite": True, "restore_snapshot": "host/other/fixture"},
                    "must belong to the requested host backup group")
                checks = [t for t in restored if any(n in t["name"] for n in
                          ["Check the staged recovery", "Refuse an incomplete archive"])]
                run(checks, {"_servarr_restore_tmp": {"path": directory}}, "lacks the Servarr database")
                (Path(directory) / f"{app}.db").write_text("fixture database")
                run(checks, {"_servarr_restore_tmp": {"path": directory}})
                run([persist], {"_servarr_backup_key": "archived-key"})
                assert "<ApiKey>archived-key</ApiKey>" in config_path.read_text()
                run(key_tasks, {"_servarr_restore_tmp": {"path": directory}})
                assert compose.read_text().count("=archived-key") == 2
                assert "/mnt/media:/mnt/media" in compose.read_text()
                # Final Deploy's production key resolution must prefer the archive to a fresh vault key.
                main_tasks = yaml.safe_load((repo / "ansible/roles/servarr/tasks/main.yml").read_text())
                resolve_key = next(t for t in main_tasks if t["name"] == "Resolve the API key")
                run([resolve_key, {"ansible.builtin.assert": {"that": ["_servarr_api_key == 'archived-key'"]}}],
                    {"_servarr_config_existing": {"content": base64.b64encode(config_path.read_bytes()).decode()},
                     "homelabinfra_vault": {"media": {app: {"api_key": "fresh-key"}}}})
                config_path.write_text("<Config/>")
                run(key_tasks, {"_servarr_restore_tmp": {"path": directory}}, failure="lacks ApiKey")
    finally:
        server.shutdown()
        server.server_close()
    print("Servarr recovery: four-app shared dispatch, preview, group guards and key continuity passed")


if __name__ == "__main__":
    main()
