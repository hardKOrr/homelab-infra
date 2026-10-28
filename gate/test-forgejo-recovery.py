#!/usr/bin/env python3
"""Exercise Forgejo's paired PBS archive and transactional restore rollback."""

import copy
import http.server
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
defaults_source = yaml.safe_load((repo / "ansible/vars/app-defaults/forgejo.yml").read_text())
catalog_source = yaml.safe_load((repo / "catalog/applications.yml").read_text())
backup_source = yaml.safe_load((repo / "ansible/roles/forgejo/tasks/backup.yml").read_text())
restore_tasks_source = (repo / "ansible/roles/forgejo/tasks/restore.yml").read_text()
restore_source = yaml.safe_load(restore_tasks_source)
scrub_failure_task = repo / "ansible/tasks/fail-with-scrubbed-output.yml"
compose_template = (repo / "ansible/roles/forgejo/templates/docker-compose.yml.j2").read_text()
forgejo_tasks_source = (repo / "ansible/roles/forgejo/tasks/main.yml").read_text()


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(self.server.response_status)
        self.end_headers()

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root, case):
    """Stub PBS and Compose boundaries while running the production filesystem flow."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        task.pop("no_log", None)
        if (case == "archive-extraction-launch-failure"
                and task.get("name") == "Forgejo restore | Extract the selected PBS archive"):
            task["ansible.builtin.command"]["argv"][0] = str(root / "bin/missing-pbs-client")
        if task.get("ansible.builtin.include_tasks", "").endswith(
            "fail-with-scrubbed-output.yml"
        ):
            task["ansible.builtin.include_tasks"] = str(scrub_failure_task)
        include = task.get("ansible.builtin.include_tasks", "")
        if "/tasks/database/" in include:
            task["ansible.builtin.include_tasks"] = str(
                repo / "gate/fixtures/database" / Path(include).name)
        if task.get("ansible.builtin.include_tasks", "").endswith("resolve-pbs-target.yml"):
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True,
                "k8s_pbs_repository": "forgejo-user@pbs.example.invalid:forgejo",
                "k8s_pbs_fingerprint": "pbs-fingerprint-123",
                "k8s_pbs_password": "" if case == "archive-extraction-empty-password" else "test-only",
            }
        if "community.docker.docker_compose_v2" in task:
            compose = task.pop("community.docker.docker_compose_v2")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/service"), compose["state"], *compose["services"]]
            }
            task["changed_when"] = True
        if "ansible.builtin.uri" in task:
            task["retries"] = 1
            task["delay"] = 0
        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root, case)
    return tasks


def environment(root, case):
    return os.environ | {
        "ANSIBLE_STDOUT_CALLBACK": "default",
        "ANSIBLE_NOCOLOR": "1",
        "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
        "FORGEJO_TEST_CASE": case,
        "FORGEJO_TEST_ROOT": str(root),
    }


def run_play(root, tasks, variables, case, server=None):
    playbook = root / "run.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": variables,
        "tasks": fixture_tasks(tasks, root, case),
    }]))
    if server is not None:
        server.response_status = 503 if case == "health-failure" else 200
        variables["app_config"]["app"]["port"] = server.server_port
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": variables,
            "tasks": fixture_tasks(tasks, root, case),
        }]))
    return subprocess.run(
        [str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
        env=environment(root, case), text=True, capture_output=True, check=False,
    )


def make_stubs(root):
    (root / "bin").mkdir()
    scripts = {
        "service": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['forgejo']
with Path(os.environ['FORGEJO_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\n')
if sys.argv[1]=='present' and os.environ['FORGEJO_TEST_CASE']=='start-failure':
    raise SystemExit(7)
''',
        "docker": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
case=os.environ['FORGEJO_TEST_CASE']
root=Path(os.environ['FORGEJO_TEST_ROOT'])
script=args[-1] if args else ''
volumes=[args[i+1] for i,value in enumerate(args[:-1]) if value=='--volume']
if 'proxmox-backup-client backup data.pxar:/archive' in script:
    assert 'data.pxar:/archive --all-file-systems true' in script, script
    assert script.count('proxmox-backup-client backup ') == 1, script
    assert 'database.pxar' not in script, script
    assert '--keep-last 2' in script, script
    assert '--keep-daily' in script, script
    assert '--backup-id "$BACKUP_ID"' in script, script
    assert any(v.endswith(':/archive:ro') for v in volumes), volumes
    assert any(v == str(root/'data')+':/archive/forgejo:ro' for v in volumes), volumes
    if case=='backup-upload-failure':
        raise SystemExit(7)
    raise SystemExit(0)
if 'proxmox-backup-client snapshot list' in script:
    print('host/forgejo/2026-10-13T00:00:00Z')
    raise SystemExit(0)
if 'proxmox-backup-client restore' in script:
    scratch=Path(next(v.split(':',1)[0] for v in volumes if v.endswith(':/restore')))
    if case in ['archive-extraction-failure', 'archive-extraction-empty-password']:
        print('old stderr line should be omitted', file=sys.stderr)
        password='test-only' if case == 'archive-extraction-failure' else ''
        print(f'credentials={password} repository=forgejo-user@pbs.example.invalid:forgejo fingerprint=pbs-fingerprint-123 snapshot=host/forgejo/2026-10-13T00:00:00Z key=https://pbs.example.invalid:8007/proxmox-backup/file-restore.gpg apt=https://apt.example.invalid/debian/pbs endpoint=pbs.example.invalid config=/config/private/client.key relative=forgejo/repositories/alice/canary.git', file=sys.stderr)
        print('diagnostic URL=https://diagnostics.example.invalid/details and file=/var/lib/forgejo/archive', file=sys.stderr)
        print('final stderr detail: HTTP 503 from worker-7', file=sys.stderr)
        print('EXTRACTION STDOUT MUST APPEAR')
        raise SystemExit(7)
    archive=scratch/'archive'
    if 'install -d -m 0755 /etc/apt/keyrings /restore/archive' in script:
        archive.mkdir(parents=True,exist_ok=True)
    if archive.exists() and '--allow-existing-dirs true' not in script:
        print('restore target already exists', file=sys.stderr)
        raise SystemExit(7)
    archive.mkdir(exist_ok=True)
    (archive/'database').mkdir(parents=True,exist_ok=True)
    (archive/'database/forgejo.dump').write_text('recovered database\n')
    data=archive/'forgejo'
    (data/'gitea/conf').mkdir(parents=True,exist_ok=True)
    (data/'gitea/conf/app.ini').write_text('recovered configuration secret\n')
    (data/'forgejo-repositories/canary.git').mkdir(parents=True,exist_ok=True)
    (data/'forgejo-repositories/canary.git/HEAD').write_text('recovered commit\n')
    if case=='incomplete-archive':
        (data/'gitea/conf/app.ini').unlink()
    raise SystemExit(0)
raise SystemExit(11)
''',
        "database-fixture": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
action,dump,instance,name=sys.argv[1:]
assert (instance,name)==('forgejo','forgejo'), sys.argv
case=os.environ['FORGEJO_TEST_CASE']
root=Path(os.environ['FORGEJO_TEST_ROOT'])
state=root/'db-state'
dump=Path(dump)
if action=='dump':
    assert dump.parent.is_dir(), dump
    dump.write_text('new recovery database\n' if dump.name=='forgejo.dump' else state.read_text())
elif action=='verify-dump':
    assert dump.read_text(), dump
elif dump.name=='forgejo.dump':
    if case in ['db-failure-after-move', 'rollback-database-failure']:
        state.write_text('partial recovered database\n')
        raise SystemExit(7)
    state.write_text('recovered database\n')
elif dump.name=='database-before.dump':
    if case=='rollback-database-failure':
        raise SystemExit(8)
    state.write_text(dump.read_text())
else:
    raise SystemExit(10)
''',
    }
    for name, script in scripts.items():
        path = root / "bin" / name
        path.write_text(script)
        path.chmod(0o700)


def static_contract():
    defaults = defaults_source["forgejo_defaults"]
    # The standard Forgejo image needs its s6 init to start as root; USER_UID/GID
    # in forgejo.env selects the application user after init. A Compose user override
    # prevents s6 from creating its lock and leaves the container unhealthy.
    assert not any(line.strip().startswith("user:") for line in compose_template.splitlines())
    assert "http://localhost:3000/api/healthz" in compose_template
    assert 'url: "http://localhost:{{ app_config.app.port }}/api/healthz"' in forgejo_tasks_source
    assert 'url: "http://localhost:{{ app_config.app.port }}/api/healthz"' in restore_tasks_source
    assert "install -d -m 0755 /etc/apt/keyrings /restore/archive" in restore_tasks_source
    assert "--allow-existing-dirs true" in restore_tasks_source
    assert "--tmpfs" in restore_tasks_source
    assert "/root/.cache" in restore_tasks_source
    assert "FORGEJO_RESTORE_STAGE=archive-extraction" in restore_tasks_source
    assert "_forgejo_restore_archive_failure_class" not in restore_tasks_source
    assert "_forgejo_restore_archive_safe_signature" not in restore_tasks_source
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["application_consistent"] is True
    assert "postgres_client_image" not in defaults["backup"]
    assert "backup" in catalog_source["applications"]["forgejo"]["actions"]
    assert "restore" in catalog_source["applications"]["forgejo"]["actions"]
    forward = defaults["firewall"]["inbound"][0]
    assert forward == {
        "name": "forgejo-ssh", "protocol": "TCP", "port": defaults["app"]["ssh_port"],
        "enabled": False,
    }
    playbook = yaml.safe_load((repo / "ansible/playbooks/apps/forgejo.yml").read_text())
    firewall_play = next(play for play in playbook if "Open the app's network access" in play["name"])
    include = firewall_play["tasks"][0]
    assert include["ansible.builtin.include_tasks"].endswith("firewall/open-app-access.yml")
    assert include["vars"]["firewall_inbound_ports"]["forgejo-ssh"] == "{{ _forgejo_fw_host.app_config.app.ssh_port }}"
    wire_play = next(play for play in playbook if play["name"] == "Forgejo | Wire")
    assert wire_play["vars"]["wiring_monitor_url"] == "https://{{ hostvars['localhost'].forgejo_fqdn }}/api/healthz"
    endpoint_task = next(
        task for task in wire_play["tasks"]
        if task.get("name") == "Record Forgejo endpoint for app-to-app consumers"
    )
    endpoint = Templar(
        loader=DataLoader(),
        variables={
            "instance": "forgejo-test",
            "_forgejo_host": {
                "ansible_host": "192.0.2.45",
                "app_config": {"app": {"port": 3000, "ssh_port": 2222}},
            },
        },
    ).template(endpoint_task["vars"]["generated_facts_data"])
    assert endpoint == {
        "forgejo-test": {
            "provider": "forgejo",
            "instance": "forgejo-test",
            "host": "http://192.0.2.45",
            "port": 3000,
            "url": "http://192.0.2.45:3000/",
            "ssh_port": 2222,
        }
    }, endpoint


def app_variables(root, server=None):
    data_path = root / "data"
    app = {
        "database": {"provider": "postgresql", "instance": "postgresql-forgejo",
                     "name": "forgejo", "role": "forgejo"},
        "database_user": "forgejo",
        "data_path": str(data_path),
        "port": server.server_port if server else 3000,
    }
    return {
        "instance": "forgejo",
        "restore_target": "forgejo",
        "restore_destination": "existing",
        "restore_snapshot": "host/forgejo/2026-10-13T00:00:00Z",
        "restore_overwrite": True,
        "homelabinfra_infra": {
            "backups": {"host": "pbs.example.invalid"},
            "databases": {"postgresql-forgejo": {
                "provider": "postgresql", "host": "postgres.example.invalid", "port": 5432,
            }},
        },
        "homelabinfra_vault": {"apps": {"forgejo": {
            "database_user": "forgejo", "database_password": "test-only",
        }}},
        "app_config": {
            "app": app,
            "backup": {
                "enabled": True, "application_consistent": True,
                "backup_id": "", "retention_days": 14,
                "client": {"image": "test-pbs",
                           "key_url": "https://pbs.example.invalid:8007/proxmox-backup/file-restore.gpg",
                           "repository": "https://apt.example.invalid/debian/pbs",
                           "suite": "bookworm", "component": "main"},
            },
        },
    }


def check_backup(case):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        make_stubs(root)
        data = root / "data"
        (data / "gitea/conf").mkdir(parents=True)
        (data / "gitea/conf/app.ini").write_text('secret=old\n')
        (data / "forgejo-repositories/canary.git").mkdir(parents=True)
        (data / "forgejo-repositories/canary.git/HEAD").write_text('commit old\n')
        variables = app_variables(root)
        variables["app_config"]["app"]["data_path"] = str(data)
        result = run_play(root, backup_source, variables, case)
        success = case == "backup-success"
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert (data / "gitea/conf/app.ini").read_text() == 'secret=old\n'
        assert (data / "forgejo-repositories/canary.git/HEAD").read_text() == 'commit old\n'
        assert (root / "services").read_text().splitlines() == ["stopped", "present"]


def check_scrubbed_failure_output():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        make_stubs(root)
        secret = "literal-test-secret-435"
        variables = {
            "scrub_failure_result": {
                "rc": 7,
                "stderr": (
                    f"pg_restore: could not read {secret}: archive member is corrupt\n"
                    "second stderr line"
                ),
                "stdout": f"restore started with {secret}\nprogress: 50%",
            },
            "scrub_failure_secrets": [secret],
            "scrub_failure_context": "FORGEJO_RESTORE_STAGE=archive-extraction",
        }
        result = run_play(
            root,
            [{"ansible.builtin.include_tasks": str(scrub_failure_task)}],
            variables,
            "scrubbed-failure-output",
        )
        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        assert "FORGEJO_RESTORE_STAGE=archive-extraction (exit code 7)" in output
        assert "pg_restore: could not read ***: archive member is corrupt" in output
        assert "second stderr line" in output
        assert "restore started with ***" in output
        assert "progress: 50%" in output
        assert secret not in output, output

        launch_failure = {
            "scrub_failure_result": {
                "failed": True,
                "msg": f"Unable to execute missing command with argument {secret}",
            },
            "scrub_failure_secrets": [secret],
            "scrub_failure_context": "FORGEJO_RESTORE_STAGE=archive-extraction",
        }
        result = run_play(
            root,
            [{"ansible.builtin.include_tasks": str(scrub_failure_task)}],
            launch_failure,
            "scrubbed-launch-failure",
        )
        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        assert "FORGEJO_RESTORE_STAGE=archive-extraction (exit code unknown)" in output
        assert "Unable to execute missing command with argument ***" in output
        assert secret not in output, output


def check_restore(case, server):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        make_stubs(root)
        data = root / "data"
        (data / "gitea/conf").mkdir(parents=True)
        (data / "gitea/conf/app.ini").write_text('original config secret\n')
        (data / "forgejo-repositories").mkdir()
        (data / "forgejo-repositories/original.git").write_text('original repository\n')
        (root / "db-state").write_text('original database\n')
        variables = app_variables(root, server)
        if case == "plan":
            variables["restore_overwrite"] = False
            variables["restore_snapshot"] = ""
        if case == "foreign-group":
            variables["restore_overwrite"] = False
            variables["restore_snapshot"] = "host/other/2026-10-13T00:00:00Z"
        result = run_play(root, restore_source, variables, case, server)
        success = case in ["plan", "success"]
        assert (result.returncode == 0) == success, result.stdout + result.stderr

        if case in ["archive-extraction-failure", "archive-extraction-empty-password"]:
            output = result.stdout + result.stderr
            assert "exit code 7" in output
            assert "FORGEJO_RESTORE_STAGE=archive-extraction" in output
            if case == "archive-extraction-failure":
                assert "credentials=***" in output
            else:
                assert "credentials= repository=***" in output
            assert "fingerprint=***" in output
            assert "test-only" not in output, "\n".join(
                line for line in output.splitlines() if "test-only" in line
            )
            assert "repository=***" in output
            assert "snapshot=***" in output
            assert "key=***" in output
            assert "apt=***" in output
            assert "endpoint=***" in output
            assert "config=[config path redacted]" in output
            assert "forgejo-user@pbs.example.invalid:forgejo" not in output
            assert "host/forgejo/2026-10-13T00:00:00Z" not in output
            assert "/config/private/client.key" not in output
            assert "forgejo/repositories/alice/canary.git" in output
            assert "https://diagnostics.example.invalid/details" in output
            assert "/var/lib/forgejo/archive" in output
            assert "EXTRACTION STDOUT MUST APPEAR" in output
            assert "old stderr line should be omitted" in output
            assert "final stderr detail: HTTP 503 from worker-7" in output

        if case == "archive-extraction-launch-failure":
            output = result.stdout + result.stderr
            assert "FORGEJO_RESTORE_STAGE=archive-extraction" in output
            assert "exit code 2" in output
            assert "No such file or directory" in output
            assert (data / "gitea/conf/app.ini").read_text() == 'original config secret\n'
            assert (data / "forgejo-repositories/original.git").read_text() == 'original repository\n'
            assert (root / "db-state").read_text() == 'original database\n'
            assert not (root / "services").exists(), "launch failure proceeded to mutation"

        if case == "success":
            assert not (data / "forgejo-repositories/original.git").exists()
            assert (data / "forgejo-repositories/canary.git/HEAD").read_text() == 'recovered commit\n'
            assert (data / "gitea/conf/app.ini").read_text() == 'recovered configuration secret\n'
            assert (root / "db-state").read_text() == 'recovered database\n'
            assert not list(root.glob(".forgejo-forgejo-restore-*"))
        elif case in ["db-failure-after-move", "health-failure"]:
            assert (data / "gitea/conf/app.ini").read_text() == 'original config secret\n'
            assert (data / "forgejo-repositories/original.git").read_text() == 'original repository\n'
            assert not (data / "forgejo-repositories/canary.git").exists()
            assert (root / "db-state").read_text() == 'original database\n'
            assert not list(root.glob(".forgejo-forgejo-restore-*"))
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped" and states[-1] == "stopped", states
        elif case == "rollback-database-failure":
            assert (data / "gitea/conf/app.ini").read_text() == 'original config secret\n'
            assert (data / "forgejo-repositories/original.git").read_text() == 'original repository\n'
            retained = list(root.glob(".forgejo-forgejo-restore-*/database-before.dump"))
            assert len(retained) == 1
            assert retained[0].read_text() == 'original database\n'
            assert (root / "db-state").read_text() == 'partial recovered database\n'
        elif case != "archive-extraction-launch-failure":
            assert (data / "gitea/conf/app.ini").read_text() == 'original config secret\n'
            assert (data / "forgejo-repositories/original.git").read_text() == 'original repository\n'
            assert (root / "db-state").read_text() == 'original database\n'
            assert not (root / "services").exists(), "plan or rejected point stopped Forgejo"


def main():
    static_contract()
    check_scrubbed_failure_output()
    check_backup("backup-success")
    check_backup("backup-upload-failure")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["plan", "foreign-group", "success", "archive-extraction-failure",
                     "archive-extraction-launch-failure",
                     "archive-extraction-empty-password", "db-failure-after-move",
                     "health-failure", "rollback-database-failure"]:
            check_restore(case, server)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    print("Forgejo recovery checks passed")


if __name__ == "__main__":
    main()
