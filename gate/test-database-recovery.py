#!/usr/bin/env python3
"""Exercise the shared database dump, restore and drop for each engine.

- scripts/database-client.sh runs against command shims for pg_dump/pg_restore/psql and
  the MariaDB and MySQL clients, so each engine's dump and restore is checked end to end.
- guard-app-database.yml's real assert conditions are rendered against fixture facts.
- remove.yml checks ownership before unwiring and drops through drop.yml for every app
  whose database lives on a registered backend.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
DATABASE = ROOT / "ansible/tasks/database"
CLIENT = DATABASE / "scripts/database-client.sh"

# Each shim logs its argv (one line) and stdin to $LOG, then plays its engine's part.
SHIM = """#!/bin/sh
name=$(basename "$0")
printf '%s %s\\n' "$name" "$(printf '%s' "$*" | tr '\\n' ' ')" >> "$LOG"
case "$name" in
  pg_dump)
    for arg; do case "$arg" in --file=*) printf 'PGDMP' > "${arg#--file=}" ;; esac; done ;;
  pg_restore)
    case "$1" in
      --list)
        printf ';\\n; Archive\\n3; 3079 16385 EXTENSION - vector\\n'
        printf '4123; 0 0 COMMENT - EXTENSION vector\\n'
        printf '215; 1259 16386 TABLE public items app\\n' ;;
      *)
        [ "${FAIL_RENDER:-0}" = 1 ] && exit 3
        for arg; do case "$arg" in --use-list=*) cp "${arg#--use-list=}" "$LOG.list" ;; esac; done
        printf 'CREATE TABLE public.items ();\\n' ;;
    esac ;;
  psql)
    for arg; do case "$arg" in --file=*) cp "${arg#--file=}" "$LOG.sql" ;; esac; done ;;
  mariadb-dump | mysqldump)
    printf 'CREATE DATABASE `source`;\\nUSE `source`;\\nCREATE TABLE `items` (id int);\\n' ;;
  mariadb | mysql)
    case "$*" in
      *information_schema.SCHEMATA*) echo 'utf8mb4 COLLATE utf8mb4_unicode_ci' ;;
      *--database=*) cat > "$LOG.load" ;;
    esac ;;
esac
"""

ENV = {"DB_HOST": "192.0.2.10", "DB_PORT": "5432", "DB_NAME": "app",
       "DB_USER": "app", "DB_PASSWORD": "s3cretpw"}


def run_client(engine, action, file, work, **extra):
    shims = work / "bin"
    shims.mkdir(exist_ok=True)
    for name in ["pg_dump", "pg_restore", "psql", "mariadb", "mariadb-dump", "mysql", "mysqldump"]:
        (shims / name).write_text(SHIM)
        (shims / name).chmod(0o755)
    log = work / "log"
    log.write_text("")
    env = {"PATH": f"{shims}:/usr/bin:/bin", "LOG": str(log), "DB_ENGINE": engine,
           **ENV, **extra}
    result = subprocess.run(["sh", str(CLIENT), action, str(file)], env=env,
                            capture_output=True, text=True, check=False)
    return result, log.read_text().splitlines()


def check_postgresql():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        dump = work / "app.dump"
        result, calls = run_client("postgresql", "dump", dump, work)
        assert result.returncode == 0, result.stderr
        assert dump.read_text() == "PGDMP"
        assert calls[0].startswith("pg_dump --format=custom --no-owner --no-acl")
        assert ENV["DB_PASSWORD"] not in calls[0], "password must not be on the command line"

        result, calls = run_client("postgresql", "restore", dump, work)
        assert result.returncode == 0, result.stderr
        listing = (work / "log.list").read_text()
        assert "EXTENSION" not in listing, "provision.yml owns extensions, not the restore"
        assert "TABLE public items" in listing
        script = (work / "log.sql").read_text()
        assert script.startswith("DROP OWNED BY CURRENT_USER;\n")
        assert "CREATE TABLE public.items" in script
        psql = next(call for call in calls if call.startswith("psql "))
        assert "--single-transaction" in psql and "--set=ON_ERROR_STOP=1" in psql

        (work / "log.sql").unlink()
        result, calls = run_client("postgresql", "restore", dump, work, FAIL_RENDER="1")
        assert result.returncode != 0
        assert not any(call.startswith("psql ") for call in calls), (
            "a failed render must never reach psql")


def check_mysql_compatible(engine, dump_command, client):
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        dump = work / "app.sql"
        result, calls = run_client(engine, "dump", dump, work, DB_PORT="3306")
        assert result.returncode == 0, result.stderr
        assert "CREATE TABLE" in dump.read_text()
        call = calls[0]
        assert call.startswith(f"{dump_command} --defaults-extra-file=")
        assert "--single-transaction" in call and call.endswith(" app")
        assert ENV["DB_PASSWORD"] not in call, "password must stay in the option file"
        assert ("--set-gtid-purged=OFF" in call) == (engine == "mysql")
        assert ("--routines" in call) == (engine == "mariadb")

        result, calls = run_client(engine, "restore", dump, work, DB_PORT="3306")
        assert result.returncode == 0, result.stderr
        recreate = next(call for call in calls if "DROP DATABASE" in call)
        assert recreate.startswith(f"{client} ")
        assert "CREATE DATABASE `app` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" in recreate
        loaded = (work / "log.load").read_text()
        assert loaded.startswith("CREATE TABLE") and "USE" not in loaded, (
            "a dump must never switch the restore to its source database")
        assert calls.index(recreate) < next(
            i for i, call in enumerate(calls) if "--database=app" in call)


def check_client_refuses_unsafe_input():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        result, calls = run_client("postgresql", "dump", work / "x", work, DB_NAME="app;drop")
        assert result.returncode == 64 and not calls
        result, calls = run_client("sqlite", "dump", work / "x", work)
        assert result.returncode == 64 and not calls


def tasks(path):
    return yaml.safe_load(path.read_text())


def all_tasks(task_list):
    for task in task_list:
        yield task
        for key in ["block", "rescue", "always"]:
            yield from all_tasks(task.get(key, []))


def task_named(task_list, name):
    found = next((task for task in all_tasks(task_list) if task.get("name") == name), None)
    assert found, f"task not found: {name}"
    return found


def render(expression, variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def guard_passes(variables):
    guard = tasks(DATABASE / "guard-app-database.yml")
    facts = task_named(guard, "Database guard | Find other applications using this database or login")
    variables = dict(variables)
    variables["_other_items"] = render(facts["vars"]["_other_items"], variables)
    for name, expression in facts["ansible.builtin.set_fact"].items():
        variables[name] = render(expression, variables)
    checks = [
        task_named(guard, "Database guard | Refuse a shared or mismatched database"),
        task_named(guard, "Database guard | Require one owned backend guest at the registered address"),
    ]
    if variables["database_app_target"]["provider"] == "postgresql":
        checks.append(task_named(guard, "Database guard | Refuse a PostgreSQL database owned by another role"))
    return all(render("{{ (%s) }}" % condition, variables)
               for task in checks
               for condition in ([task["ansible.builtin.assert"]["that"]]
                                 if isinstance(task["ansible.builtin.assert"]["that"], str)
                                 else task["ansible.builtin.assert"]["that"]))


def guard_fixture(provider, **changes):
    variables = {
        "database_app_instance": "app",
        "database_app_target": {"provider": provider, "instance": "db-app",
                                "host": "192.0.2.10", "name": "app", "role": "app"},
        "homelabinfra_vault": {"apps": {
            "app": {"database_host": "192.0.2.10", "database_name": "app", "database_user": "app"},
            "other": {"database_host": "192.0.2.10", "database_name": "other", "database_user": "other"},
        }},
        "tag_group_name": "lab_app_db_app",
        "groups": {"lab_app_db_app": ["db-app"], "lab_managed": ["db-app"]},
        "hostvars": {"db-app": {"ansible_host": "192.0.2.10"}},
        "_database_guard_owner": {"stdout": "app\n"},
    }
    for key, value in changes.items():
        variables[key] = value
    return variables


def check_ownership_guard():
    for provider in ["postgresql", "mariadb"]:
        assert guard_passes(guard_fixture(provider)), provider
        vault = guard_fixture(provider)["homelabinfra_vault"]
        vault["apps"]["other"]["database_name"] = "app"
        assert not guard_passes(guard_fixture(provider, homelabinfra_vault=vault)), (
            f"{provider}: a database another app names must not be touched")
        vault = guard_fixture(provider)["homelabinfra_vault"]
        vault["apps"]["other"]["database_user"] = "app"
        assert not guard_passes(guard_fixture(provider, homelabinfra_vault=vault)), (
            f"{provider}: a login another app uses must not be touched")
        vault = guard_fixture(provider)["homelabinfra_vault"]
        vault["apps"]["app"]["database_name"] = "elsewhere"
        assert not guard_passes(guard_fixture(provider, homelabinfra_vault=vault))
        assert not guard_passes(guard_fixture(provider, groups={
            "lab_app_db_app": ["db-app"], "lab_managed": []})), "untagged backend guest"
        assert not guard_passes(guard_fixture(provider, groups={
            "lab_app_db_app": ["db-app", "db-app-2"], "lab_managed": ["db-app", "db-app-2"]}))
        assert not guard_passes(guard_fixture(provider, hostvars={
            "db-app": {"ansible_host": "192.0.2.99"}})), "address must match the owned guest"
        assert not guard_passes(guard_fixture(provider, tag_group_name="")), "outside the lab lane"
    assert guard_passes(guard_fixture("postgresql", _database_guard_owner={"stdout": "missing"}))
    assert not guard_passes(guard_fixture("postgresql", _database_guard_owner={"stdout": "postgres"}))


def check_destructive_files_use_the_guard():
    for name in ["drop.yml", "restore.yml"]:
        steps = [task.get("ansible.builtin.include_tasks") for task in tasks(DATABASE / name)]
        assert steps[:2] == ["resolve-app-database.yml", "guard-app-database.yml"], name
    restore = tasks(DATABASE / "restore.yml")
    provision = task_named(restore, "Database restore | Create the database, login, grants and extensions")
    assert provision["ansible.builtin.include_tasks"] == "provision.yml"

    drop = tasks(DATABASE / "drop.yml")
    mutations = [
        task_named(drop, "Database drop | Drop the PostgreSQL database"),
        task_named(drop, "Database drop | Drop the PostgreSQL role"),
        task_named(drop, "Database drop | Drop the MariaDB/MySQL database"),
        task_named(drop, "Database drop | Drop the MariaDB/MySQL login on every permitted client network"),
    ]
    assert all(task["delegate_to"] == "database_app_target" for task in mutations)
    assert mutations[3]["loop"] == "{{ database_app_target.client_hosts }}"
    assert "WITH (FORCE)" in mutations[0]["ansible.builtin.command"]["argv"][-1]


def check_client_image_matches_server():
    resolve = tasks(DATABASE / "resolve-app-database.yml")
    record = task_named(resolve, "Database target | Record the target and its matching client image")
    for provider, stdout, expected in [
        ("postgresql", "160004", "docker.io/library/postgres:16-alpine"),
        ("mariadb", "10.11.6-MariaDB-0+deb12u1", "docker.io/library/mariadb:10.11"),
        ("mysql", "8.4.2", "docker.io/library/mysql:8.4"),
    ]:
        variables = {
            "database_app_config": {"provider": provider},
            "_database_app_pg_version": {"stdout": stdout},
            "_database_app_mysql_version": {"stdout": stdout},
        }
        variables["_server_version"] = render(record["vars"]["_server_version"], variables)
        image = render(record["ansible.builtin.set_fact"]["database_app_target"]["client_image"],
                       variables)
        assert image.strip() == expected, (provider, image)


def check_remove_wiring():
    remove = yaml.safe_load((ROOT / "ansible/playbooks/apps/remove.yml").read_text())
    unwire = remove[0]
    decide = task_named(unwire["pre_tasks"], "Decide whether Remove drops a named database")
    pre_names = [task.get("name") for task in unwire["pre_tasks"]]
    resolve_index = pre_names.index("Resolve the app's named database before unwiring")
    guard_index = pre_names.index("Check the app's named database ownership before unwiring")
    assert pre_names.index(decide["name"]) < resolve_index < guard_index
    assert all(task.get("name") != "Remove the app's Kubernetes resources"
               for task in unwire["pre_tasks"]), "the guard must run before any unwiring"

    expression = decide["ansible.builtin.set_fact"]["_rm_drops_database"]
    registered = []
    for path in sorted((ROOT / "ansible/vars/app-defaults").glob("*.yml")):
        defaults = yaml.safe_load(path.read_text()) or {}
        for config in defaults.values():
            database = ((config or {}).get("app") or {}).get("database")
            if not isinstance(database, dict):
                continue
            drops = str(render(expression, {"delete_data": True, "app_config": {"app": {"database": database}}, "instance": path.stem})).strip()
            assert drops == str("provider" in database and "instance" in database), path.name
            if drops == "True":
                registered.append(path.stem)
            kept = str(render(expression, {"delete_data": False, "app_config": {"app": {"database": database}}, "instance": path.stem})).strip()
            assert kept == "False", path.name
    assert {"bookstack", "immich", "litellm", "plane", "wordpress"} <= set(registered)

    drop_play = next(play for play in remove if play["name"] == "Remove App | Drop its named database")
    assert drop_play["hosts"] == "localhost", "the backend must be reached without the app host"
    assert remove.index(drop_play) > next(
        index for index, play in enumerate(remove) if play["name"].startswith("Remove App | Stop"))
    drop = drop_play["tasks"][0]
    assert drop["ansible.builtin.include_tasks"] == "../../tasks/database/drop.yml"
    assert drop["when"] == "_rm_drops_database | default(false) | bool"


def main():
    check_postgresql()
    check_mysql_compatible("mariadb", "mariadb-dump", "mariadb")
    check_mysql_compatible("mysql", "mysqldump", "mysql")
    check_client_refuses_unsafe_input()
    check_ownership_guard()
    check_destructive_files_use_the_guard()
    check_client_image_matches_server()
    check_remove_wiring()
    print("database recovery: dump, restore and drop per engine, ownership guard and Remove wiring passed")


if __name__ == "__main__":
    main()
