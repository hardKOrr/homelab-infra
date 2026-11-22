#!/usr/bin/env python3
"""Exercise Forgejo's paired PBS archive and check its restore-swap inputs and hook.

gate/test-restore-swap.py proves the swap, the database dump, load and rollback.

Checks: Forgejo's quiesced data archive, and its restore swap inputs with the database dump
and the staged hook refusing a point without `app.ini`.
"""

import copy
import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

from pbs_client_stub import stub_pbs_client
from restore_swap_inputs import run_hook, swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
defaults_source = yaml.safe_load((repo / "ansible/vars/app-defaults/forgejo.yml").read_text())
backup_source = yaml.safe_load((repo / "ansible/roles/forgejo/tasks/backup.yml").read_text())
hook_source = yaml.safe_load((repo / "ansible/roles/forgejo/tasks/backup-quiesced.yml").read_text())
compose_template = (repo / "ansible/roles/forgejo/templates/docker-compose.yml.j2").read_text()
forgejo_tasks_source = "".join((repo / f"ansible/roles/forgejo/tasks/{name}.yml").read_text().removeprefix("---\n")
                               for name in ("prepare", "docker", "admin"))


def fixture_tasks(tasks, root):
    """Stub PBS, database and Compose boundaries while running the production filesystem flow."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        task.pop("no_log", None)
        stub_pbs_client(task, root / "bin/proxmox-backup-client")
        include = task.get("ansible.builtin.include_tasks", "")
        if "/tasks/database/" in include:
            task["ansible.builtin.include_tasks"] = str(
                repo / "gate/fixtures/database" / Path(include).name)
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
                task[key] = fixture_tasks(task[key], root)
    return tasks


def all_tasks(tasks):
    for task in tasks:
        yield task
        for key in ["block", "rescue", "always"]:
            yield from all_tasks(task.get(key, []))


def included(tasks, name):
    return [task for task in all_tasks(tasks)
            if task.get("ansible.builtin.include_tasks", "").endswith(name)]


def environment(root, case):
    return os.environ | {
        "ANSIBLE_STDOUT_CALLBACK": "default",
        "ANSIBLE_NOCOLOR": "1",
        "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
        "FORGEJO_TEST_CASE": case,
        "FORGEJO_TEST_ROOT": str(root),
    }


def run_play(root, tasks, variables, case):
    playbook = root / "run.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": variables,
        "tasks": fixture_tasks(tasks, root),
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
        "proxmox-backup-client": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
case=os.environ['FORGEJO_TEST_CASE']
root=Path(os.environ['FORGEJO_TEST_ROOT'])
if args[0]=='backup':
    archive_root=root/'.pbs-backup-root'
    assert args==['backup', f'data.pxar:{archive_root}'], args
    assert sorted(p.name for p in archive_root.iterdir())==['database','forgejo']
    raise SystemExit(7 if case=='backup-upload-failure' else 0)
if args[:2]==['snapshot','list']:
    print('host/forgejo/2026-10-13T00:00:00Z')
    raise SystemExit(0)
raise SystemExit(9)
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
    assert "client" not in defaults["backup"], "the shared client owns its pins"
    [upload] = included(backup_source, "pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "{{ app_config.hosting.method }}"
    assert upload["vars"]["pbs_client_stop_services"] == ["{{ forgejo_service }}"]
    assert upload["vars"]["pbs_client_quiesced_hook"] == "{{ role_path }}/tasks/backup-quiesced.yml"
    assert upload["vars"]["pbs_client_backup_args"] == ["--all-file-systems", "true"]
    assert upload["vars"]["pbs_client_backup_mounts"] == [{
        "path": "{{ _forgejo_backup_data_path }}", "target": "{{ _forgejo_backup_root }}/forgejo"}]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["application_consistent"] is True
    assert "postgres_client_image" not in defaults["backup"]
    forward = defaults["firewall"]["inbound"][0]
    assert forward == {
        "name": "forgejo-ssh", "protocol": "TCP", "port": defaults["app"]["ssh_port"],
        "enabled": False,
    }
    assert defaults["firewall"]["methods"] == ["docker", "native"]
    assert defaults["routing"]["monitor_path"] == "/api/healthz"
    prepare = yaml.safe_load((repo / "ansible/roles/forgejo/tasks/deploy-prepare.yml").read_text())
    assert prepare[0]["ansible.builtin.set_fact"]["firewall_inbound_ports"] == {
        "forgejo-ssh": "{{ app_config.app.ssh_port }}"}
    record = yaml.safe_load((repo / "ansible/roles/forgejo/tasks/deploy-record.yml").read_text())
    endpoint_task = next(
        task for task in record
        if task.get("name") == "Forgejo | Record the endpoint for app-to-app consumers"
    )
    variables = {
        "instance": "forgejo-test",
        "_deploy_fqdn": "forgejo-test.example.test",
        "app_config": {"hosting": {"method": "docker"}, "app": {"port": 3000, "ssh_port": 2222}},
        "groups": {"deploy_forgejo-test": ["stack"]},
        "hostvars": {"stack": {"ansible_host": "192.0.2.45"}},
    }
    endpoint = Templar(loader=DataLoader(), variables=variables | endpoint_task["vars"]).template(
        endpoint_task["vars"]["generated_facts_data"])
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


def app_variables(root):
    data_path = root / "data"
    app = {
        "database": {"provider": "postgresql", "instance": "postgresql-forgejo",
                     "name": "forgejo", "role": "forgejo"},
        "database_user": "forgejo",
        "data_path": str(data_path),
        "port": 3000,
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
        result = run_play(root, backup_source, variables, case)
        success = case == "backup-success"
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert (data / "gitea/conf/app.ini").read_text() == 'secret=old\n'
        assert (data / "forgejo-repositories/canary.git/HEAD").read_text() == 'commit old\n'
        assert not (root / ".pbs-backup-root").exists(), "the dump outlived the backup"


def check_hook():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        make_stubs(root)
        (root / ".pbs-backup-root/database").mkdir(parents=True)
        variables = app_variables(root) | {"_forgejo_backup_root": str(root / ".pbs-backup-root")}
        result = run_play(root, hook_source, variables, "hook")
        assert result.returncode == 0, result.stdout + result.stderr
        dump = root / ".pbs-backup-root/database/forgejo.dump"
        assert dump.read_text() == 'new recovery database\n'


def check_restore_swap():
    swap = swap_inputs("forgejo", "restore-guest.yml")
    assert swap["recovery_swap_runtime"] == "{{ app_config.hosting.method }}"
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ _forgejo_restore_data_path }}", "source": "data/forgejo",
        "owner": "{{ forgejo_data_owner }}", "group": "{{ forgejo_data_owner }}", "mode": "0750"}]
    assert swap["recovery_swap_database"] == {
        "config": "{{ app_config.app.database }}", "dump": "data/database/forgejo.dump"}
    assert swap["recovery_swap_stop_services"] == ["{{ forgejo_service }}"]
    assert swap["recovery_swap_compose_project"] == "{{ forgejo_project_dir }}"
    assert swap["recovery_swap_health_url"] == "http://localhost:{{ app_config.app.port }}/api/healthz"
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        config = stage / "data/forgejo/gitea/conf/app.ini"
        config.parent.mkdir(parents=True)
        variables = {"recovery_swap_stage": str(stage)}
        assert run_hook("forgejo", "staged", variables).returncode != 0, "no app.ini"
        config.write_text("")
        assert run_hook("forgejo", "staged", variables).returncode != 0, "empty app.ini"
        config.write_text("[server]\n")
        result = run_hook("forgejo", "staged", variables)
        assert result.returncode == 0, result.stdout + result.stderr


def check_install():
    """Deploy finishes Forgejo's install: lock, a new install's secret key, one administrator."""
    tasks = {task.get("name"): task for task in yaml.safe_load(forgejo_tasks_source) if "name" in task}
    env = (repo / "ansible/roles/forgejo/templates/forgejo.env.j2").read_text()
    assert "FORGEJO__security__INSTALL_LOCK=true" in env and "SECRET_KEY" not in env
    seed = tasks["Forgejo | Seed a new install's secret key"]
    assert seed["when"] == "not _forgejo_app_ini.stat.exists", "an existing app.ini keeps its key"
    assert "notify" not in seed, "the key lands before the first start"
    # Docker starts once through the shared Compose deploy, and the restart handler is
    # native-only and skipped when native.yml just started the service: neither restarts a
    # new install mid schema creation.
    assert tasks["Forgejo | Deploy the Compose project"]["ansible.builtin.include_tasks"] \
        .endswith("tasks/stack/compose-deploy.yml")
    restart = yaml.safe_load((repo / "ansible/roles/forgejo/handlers/main.yml").read_text())[0]
    assert "not (_forgejo_start.changed | default(false))" in restart["when"], \
        "never restart a service native.yml just started (a new install's schema creation)"
    assert restart["vars"]["restart_runtime"] == "native"
    create = tasks["Forgejo | Create the administrator"]
    listing = ("ID   Username   Email                     IsActive IsAdmin 2FA\n"
               "1    {name}   someone@example.invalid  true     true    false\n")
    with tempfile.TemporaryDirectory() as directory:
        for name, created in [("drill", True), ("homelab", False), ("homelab2", True)]:
            play = Path(directory) / "play.yml"
            play.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"app_config": {"app": {"admin_user": "homelab"}},
                         "_forgejo_admins": {"stdout": listing.format(name=name)}},
                "tasks": [{"ansible.builtin.debug": {"msg": "create"}, "when": create["when"],
                           "register": "_result"},
                          {"ansible.builtin.assert": {"that": f"(_result is skipped) == {not created}"}}],
            }]))
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                                    text=True, capture_output=True, check=False)
            assert result.returncode == 0, (name, result.stdout + result.stderr)


def main():
    static_contract()
    check_install()
    check_hook()
    check_backup("backup-success")
    check_backup("backup-upload-failure")
    check_restore_swap()
    print("Forgejo recovery checks passed")


if __name__ == "__main__":
    main()
