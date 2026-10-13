#!/usr/bin/env python3
"""Exercise Karakeep's single-archive backup and two-directory restore rollback."""

import copy
import http.server
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
backup_source = yaml.safe_load((repo / "ansible/roles/karakeep/tasks/backup.yml").read_text())
restore_source = yaml.safe_load((repo / "ansible/roles/karakeep/tasks/restore.yml").read_text())


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(self.server.response_status)
        self.end_headers()

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    """Keep production path and command tasks; stub only PBS and Compose boundaries."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        if task.get("name") == "Karakeep backup | Push one data archive and retain the newest points":
            # Test values are synthetic; unhide this boundary so a stub failure is useful.
            task["no_log"] = False
        if task.get("ansible.builtin.include_tasks", "").endswith("resolve-pbs-target.yml"):
            restore_resolver = task.get("name", "").startswith("Karakeep restore")
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True,
                "k8s_pbs_repository": ("pbs-user@pbs.example.invalid:archive"
                                       if restore_resolver else "test"),
                "k8s_pbs_fingerprint": ("pbs-fingerprint-secret"
                                         if restore_resolver else "test"),
                "k8s_pbs_password": ("pbs-password-secret" if restore_resolver else "test"),
            }
        if "community.docker.docker_compose_v2" in task:
            compose = task.pop("community.docker.docker_compose_v2")
            task["ansible.builtin.command"] = {"argv": [
                str(root / "bin/service"), compose["state"], *compose["services"]]}
            task["changed_when"] = True
        if "ansible.builtin.uri" in task:
            task["retries"] = 1
            task["delay"] = 0
        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root)
    return tasks


def all_tasks(tasks):
    for task in tasks:
        yield task
        for key in ["block", "rescue", "always"]:
            yield from all_tasks(task.get(key, []))


def environment(root, case):
    return os.environ | {
        "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
        "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
        "KARAKEEP_TEST_CASE": case, "KARAKEEP_TEST_ROOT": str(root),
    }


def run_play(root, tasks, variables, case):
    playbook = root / "run.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False, "vars": variables,
        "tasks": fixture_tasks(tasks, root),
    }]))
    return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                          env=environment(root, case), text=True, capture_output=True,
                          check=False)


def make_stubs(root):
    (root / "bin").mkdir()
    scripts = {
        "docker": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
case=os.environ['KARAKEEP_TEST_CASE']
expected_repository = ('test' if case.startswith('backup-')
                       else 'pbs-user@pbs.example.invalid:archive')
assert os.environ.get('PBS_REPOSITORY') == expected_repository
script=args[-1]
if 'proxmox-backup-client backup' in script:
    volumes=[args[i+1] for i,value in enumerate(args[:-1]) if value=='--volume']
    root=Path(os.environ['KARAKEEP_TEST_ROOT'])
    assert volumes == [str(root/'data')+':/source/data:ro',
                       str(root/'meilisearch')+':/source/meilisearch:ro'], volumes
    assert script.count('proxmox-backup-client backup ') == 1, script
    assert 'proxmox-backup-client backup data.pxar:/source --backup-id' in script, script
    assert 'proxmox-backup-client prune "host/' in script and '--keep-last 2' in script, script
    raise SystemExit(7 if case=='backup-failure' else 0)
if 'SNAPSHOT' not in os.environ:
    print('host/karakeep/2026-10-12T00:00:00Z')
    raise SystemExit(0)
if case=='extract-failure':
    print(os.environ['PBS_REPOSITORY'], os.environ['SNAPSHOT'], os.environ['PBS_PASSWORD'],
          os.environ['PBS_FINGERPRINT'], file=sys.stderr)
    raise SystemExit(7)
mount=args[args.index('--volume')+1]
assert mount.endswith(':/restore'), mount
stage=Path(mount[:-9])
assert stage.parent == Path(os.environ['KARAKEEP_TEST_ROOT']), stage
(stage/'data').mkdir()
(stage/'data'/'restored.sqlite').write_text('recovered database and assets\n')
if case!='incomplete-archive':
    (stage/'meilisearch').mkdir()
    (stage/'meilisearch'/'restored-index').write_text('recovered search index\n')
''',
        "mv": r'''#!/usr/bin/env python3
import os,subprocess,sys
from pathlib import Path
root=Path(os.environ['KARAKEEP_TEST_ROOT'])
counter=root/'mv-count'
count=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(count))
if os.environ['KARAKEEP_TEST_CASE']=='second-move-failure' and count==2:
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv',*sys.argv[1:]]).returncode)
''',
        "cp": r'''#!/usr/bin/env python3
import os,subprocess,sys
from pathlib import Path
root=Path(os.environ['KARAKEEP_TEST_ROOT'])
counter=root/'cp-count'
count=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(count))
result=subprocess.run(['/bin/cp',*sys.argv[1:]])
case=os.environ['KARAKEEP_TEST_CASE']
fail=(case=='data-copy-failure' and count==1) or (case=='search-copy-failure' and count==2)
raise SystemExit(7 if fail else result.returncode)
''',
        "chown": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
root=Path(os.environ['KARAKEEP_TEST_ROOT'])
counter=root/'chown-count'
count=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(count))
if os.environ['KARAKEEP_TEST_CASE']=='ownership-failure' and count==2:
    raise SystemExit(7)
''',
        "service": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sorted(sys.argv[2:]) == ['chrome','meilisearch','web'], sys.argv
root=Path(os.environ['KARAKEEP_TEST_ROOT'])
with (root/'services').open('a') as log:
    log.write(sys.argv[1]+'\n')
if sys.argv[1]=='present' and os.environ['KARAKEEP_TEST_CASE']=='start-failure':
    raise SystemExit(7)
''',
    }
    for name, script in scripts.items():
        path = root / "bin" / name
        path.write_text(script)
        path.chmod(0o700)


def backup_check(case):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        make_stubs(root)
        data = root / "data"
        search = root / "meilisearch"
        data.mkdir()
        search.mkdir()
        (data / "original.sqlite").write_text("original app state\n")
        (search / "original-index").write_text("original index\n")
        variables = {
            "instance": "karakeep", "karakeep_project_dir": str(root / "project"),
            "karakeep_meilisearch_data_path": str(search), "homelabinfra_infra": {"backups": {}},
            "app_config": {
                "app": {"data_path": str(data)},
                "backup": {"enabled": True, "application_consistent": True,
                           "client": {"image": "test", "key_url": "test",
                                      "repository": "test", "suite": "test",
                                      "component": "test"}},
            },
        }
        result = run_play(root, backup_source, variables, case)
        success = case == "backup-success"
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert (data / "original.sqlite").read_text() == "original app state\n"
        assert (search / "original-index").read_text() == "original index\n"
        states = (root / "services").read_text().splitlines()
        assert states == ["stopped", "present"], states


def restore_check(case, server):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        make_stubs(root)
        data = root / "data"
        search = root / "meilisearch"
        data.mkdir()
        search.mkdir()
        (data / "original.sqlite").write_text("original app state\n")
        (search / "original-index").write_text("original index\n")
        is_plan = case == "plan"
        snapshot = ("host/other-app/2026-10-12T00:00:00Z" if case == "foreign-group"
                    else "host/karakeep/2026-10-12T00:00:00Z")
        server.response_status = 503 if case == "health-failure" else 200
        variables = {
            "instance": "karakeep", "karakeep_project_dir": str(root / "project"),
            "karakeep_meilisearch_data_path": str(search),
            "restore_snapshot": snapshot, "restore_overwrite": not is_plan,
            "homelabinfra_infra": {"backups": {}},
            "app_config": {
                "app": {"data_path": str(data), "port": server.server_port},
                "backup": {"enabled": True, "application_consistent": True,
                           "client": {"image": "test", "key_url": "test",
                                      "repository": "test", "suite": "test",
                                      "component": "test"}},
            },
        }
        variables["karakeep_restore_diagnostics"] = case == "extract-failure"
        result = run_play(root, restore_source, variables, case)
        success = case in ["plan", "success"]
        assert (result.returncode == 0) == success, result.stdout + result.stderr

        if case == "success":
            assert not (data / "original.sqlite").exists()
            assert (data / "restored.sqlite").read_text() == "recovered database and assets\n"
            assert not (search / "original-index").exists()
            assert (search / "restored-index").read_text() == "recovered search index\n"
        else:
            assert (data / "original.sqlite").read_text() == "original app state\n"
            assert not (data / "restored.sqlite").exists()
            assert (search / "original-index").read_text() == "original index\n"
            assert not (search / "restored-index").exists()

        assert not list(root.glob(".karakeep-pre-restore-data-*")), result.stdout + result.stderr
        assert not list(root.glob(".karakeep-pre-restore-search-*")), result.stdout + result.stderr
        if case in ["plan", "foreign-group", "extract-failure", "incomplete-archive"]:
            assert not (root / "services").exists(), "plan or rejected archive stopped services"
        else:
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped", states
            assert states[-1] == ("present" if success else "stopped"), states
        if case == "foreign-group":
            assert "must belong to host/karakeep/" in result.stdout
        if case == "extract-failure":
            for secret in ["pbs-user@pbs.example.invalid:archive", "pbs-fingerprint-secret",
                           "pbs-password-secret", snapshot]:
                assert secret not in result.stdout, result.stdout
            for marker in ["<PBS_REPOSITORY>", "<PBS_FINGERPRINT>",
                           "<PBS_PASSWORD>", "<PBS_SNAPSHOT>"]:
                assert marker in result.stdout, result.stdout


def main():
    defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/karakeep.yml").read_text())
    config = defaults["karakeep_defaults"]
    assert config["recovery"]["methods"] == ["native"]
    assert config["backup"]["application_consistent"] is True
    assert any(item["protocol"] == "TCP" and item["port"] == 80
               for item in config["firewall"]["egress"])
    catalog = yaml.safe_load((repo / "catalog/applications.yml").read_text())
    actions = catalog["applications"]["karakeep"]["actions"]
    assert {"backup", "restore"} <= set(actions)
    assert "rollback" not in actions
    backup_task = next(task for task in all_tasks(backup_source)
                       if task.get("name") == "Karakeep backup | Push one data archive and retain the newest points")
    assert backup_task["environment"] and backup_task["changed_when"] is True
    assert backup_task["no_log"] is True
    deploy = yaml.safe_load((repo / "ansible/playbooks/apps/karakeep.yml").read_text())
    firewall_play = next(play for play in deploy if play.get("name") == "Karakeep | Open network access")
    assert any("open-app-access.yml" in task.get("ansible.builtin.include_tasks", "")
               for task in firewall_play["tasks"])

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["backup-success", "backup-failure"]:
            backup_check(case)
        for case in ["plan", "foreign-group", "extract-failure", "incomplete-archive",
                     "second-move-failure", "data-copy-failure", "search-copy-failure",
                     "ownership-failure", "start-failure", "health-failure", "success"]:
            restore_check(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Karakeep: single-archive backup, selected-group guard and two-volume rollback passed")


if __name__ == "__main__":
    main()
