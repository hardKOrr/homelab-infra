#!/usr/bin/env python3
"""Check database facts and provisioning contracts remain safe and resolvable."""

import os
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")


def render(expression, variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def database_fact_task(path):
    for document in yaml.safe_load_all(read(path)):
        for play in document if isinstance(document, list) else [document]:
            for task in play.get("tasks", []):
                if task.get("ansible.builtin.import_tasks", "").endswith(
                    "tasks/bootstrap/write-generated-facts.yml"
                ):
                    if task.get("vars", {}).get("generated_facts_service") == "databases":
                        return task
    raise AssertionError(f"database fact writer not found in {path}")


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def database_producers_resolve_instance_keys():
    fixtures = {
        "postgresql": (
            "ansible/playbooks/apps/postgresql.yml",
            "postgresql-immich",
            "_postgresql_host",
            {
                "provider": "postgresql",
                "version": "16",
                "host": "192.0.2.41",
                "port": 5432,
            },
            {"app_config": {"app": {"version": "16", "port": 5432}}, "ansible_host": "192.0.2.41"},
        ),
        "redis": (
            "ansible/playbooks/apps/redis.yml",
            "redis-immich",
            "_redis_host",
            {"provider": "redis", "host": "192.0.2.42", "port": 6379},
            {"app_config": {"app": {"port": 6379}}, "ansible_host": "192.0.2.42"},
        ),
        "mariadb": (
            "ansible/playbooks/apps/mariadb.yml",
            "mariadb-app",
            "_mariadb_host",
            {
                "provider": "mariadb",
                "host": "192.0.2.43",
                "port": 3306,
                "client_hosts": ["192.0.2.0/24"],
            },
            {
                "app_config": {"app": {"port": 3306, "client_hosts": ["192.0.2.0/24"]}},
                "ansible_host": "192.0.2.43",
            },
        ),
        "mysql": (
            "ansible/playbooks/apps/mysql.yml",
            "mysql-app",
            "_mysql_host",
            {
                "provider": "mysql",
                "host": "192.0.2.44",
                "port": 3306,
                "client_hosts": ["192.0.2.0/24"],
            },
            {
                "app_config": {"app": {"port": 3306, "client_hosts": ["192.0.2.0/24"]}},
                "ansible_host": "192.0.2.44",
            },
        ),
        "influxdb": (
            "ansible/playbooks/apps/influxdb.yml",
            "influxdb-app",
            "_influxdb_host",
            {"provider": "influxdb", "host": "192.0.2.45", "port": 8086},
            {"app_config": {"app": {"port": 8086}}, "ansible_host": "192.0.2.45"},
        ),
    }

    for provider, (path, instance, host_var, expected, host) in fixtures.items():
        task = database_fact_task(path)
        actual = render(
            task["vars"]["generated_facts_data"],
            {"instance": instance, host_var: host},
        )
        assert actual == {instance: expected}, (provider, actual)


def database_writer_removes_unresolved_keys_and_preserves_other_estates():
    writer = yaml.safe_load(read("ansible/tasks/bootstrap/write-generated-facts.yml"))
    tasks = writer
    file_task = task_named(
        tasks, "Generated facts | Remove unresolved database instance keys from file registry"
    )
    expression = file_task["ansible.builtin.set_fact"]["_gf_existing_facts"]

    default_facts = {
        "domain": "example.test",
        "databases": {
            "{{ instance }}": {"provider": "redis"},
            "postgresql-keep": {"provider": "postgresql"},
            "redis_keep.1": {"provider": "redis"},
            "invalid/name": {"provider": "mysql"},
        },
        "estates": {"foxglove": {"databases": {"mysql-keep": {"provider": "mysql"}}}},
    }
    expected_default = {
        **default_facts,
        "databases": {
            "postgresql-keep": {"provider": "postgresql"},
            "redis_keep.1": {"provider": "redis"},
        },
    }
    actual_default = render(
        expression,
        {"_gf_existing_facts": default_facts, "_generated_facts_estate": ""},
    )
    assert actual_default == expected_default, actual_default

    estate_facts = {
        "databases": {"postgresql-default": {"provider": "postgresql"}},
        "estates": {
            "foxglove": {
                "domain": "foxglove.example.test",
                "databases": {
                    "{{ instance }}": {"provider": "redis"},
                    "redis-keep": {"provider": "redis"},
                },
            },
            "other": {"databases": {"mysql-other": {"provider": "mysql"}}},
        },
    }
    actual_estate = render(
        expression,
        {"_gf_existing_facts": estate_facts, "_generated_facts_estate": "foxglove"},
    )
    expected_estate = {
        **estate_facts,
        "estates": {
            "foxglove": {
                "domain": "foxglove.example.test",
                "databases": {"redis-keep": {"provider": "redis"}},
            },
            "other": {"databases": {"mysql-other": {"provider": "mysql"}}},
        },
    }
    assert actual_estate == expected_estate, actual_estate

    memory_task = task_named(
        tasks, "Generated facts | Remove unresolved database instance keys from memory registry"
    )
    memory_task_vars = memory_task["vars"]
    roots = {
        "default": {
            "_gf_existing_infra": {
                "domain": "infra.example.test",
                "databases": {
                    "{{ instance }}": {"provider": "redis"},
                    "infra-only": {"provider": "postgresql"},
                    "infra.keep_1": {"provider": "redis"},
                },
                "estates": {
                    "foxglove": {"databases": {"infra-estate": {"provider": "mysql"}}}
                },
            },
            "_gf_existing_local_infra": {
                "domain": "localhost.example.test",
                "databases": {
                    "invalid/name": {"provider": "mysql"},
                    "localhost-only": {"provider": "redis"},
                },
                "estates": {
                    "foxglove": {"databases": {"localhost-estate": {"provider": "mysql"}}}
                },
            },
        },
        "estate": {
            "_gf_existing_infra": {
                "databases": {"infra-default": {"provider": "postgresql"}},
                "estates": {
                    "foxglove": {
                        "domain": "foxglove.example.test",
                        "databases": {
                            "{{ instance }}": {"provider": "redis"},
                            "infra-foxglove": {"provider": "mysql"},
                        },
                    },
                    "other": {"databases": {"infra-other": {"provider": "redis"}}},
                },
            },
            "_gf_existing_local_infra": {
                "databases": {"localhost-default": {"provider": "mysql"}},
                "estates": {
                    "foxglove": {
                        "domain": "foxglove.example.test",
                        "databases": {
                            "invalid/name": {"provider": "redis"},
                            "localhost-foxglove": {"provider": "postgresql"},
                        },
                    },
                    "other": {"databases": {"localhost-other": {"provider": "mysql"}}},
                },
            },
        },
    }
    expected_maps = {
        "default": {
            "_gf_existing_infra": {
                "infra-only": {"provider": "postgresql"},
                "infra.keep_1": {"provider": "redis"},
            },
            "_gf_existing_local_infra": {"localhost-only": {"provider": "redis"}},
        },
        "estate": {
            "_gf_existing_infra": {"infra-foxglove": {"provider": "mysql"}},
            "_gf_existing_local_infra": {
                "localhost-foxglove": {"provider": "postgresql"}
            },
        },
    }

    def replace_database_map(root, estate, database_map):
        result = dict(root)
        if estate:
            result["estates"] = dict(root["estates"])
            estate_registry = dict(result["estates"][estate])
            estate_registry["databases"] = database_map
            result["estates"][estate] = estate_registry
        else:
            result["databases"] = database_map
        return result

    for scope, root in roots.items():
        estate = "" if scope == "default" else "foxglove"
        for root_variable in ("_gf_existing_infra", "_gf_existing_local_infra"):
            clean_variable = (
                "_gf_clean_infra_database_registry"
                if root_variable == "_gf_existing_infra"
                else "_gf_clean_local_infra_database_registry"
            )
            cleaned_registry = render(
                memory_task_vars[clean_variable],
                {root_variable: root[root_variable], "_generated_facts_estate": estate},
            )
            assert cleaned_registry == expected_maps[scope][root_variable], cleaned_registry

            expression = memory_task["ansible.builtin.set_fact"][root_variable]
            actual = render(
                expression,
                {
                    root_variable: root[root_variable],
                    clean_variable: cleaned_registry,
                    "_generated_facts_estate": estate,
                },
            )
            expected = replace_database_map(
                root[root_variable], estate, expected_maps[scope][root_variable]
            )
            assert actual == expected, (scope, root_variable, actual)


def database_role_password_sql_uses_psql_stdin():
    tasks = yaml.safe_load(read("ansible/tasks/database/provision.yml"))
    for name in (
        "Database | Create PostgreSQL application role",
        "Database | Rotate PostgreSQL application role password",
    ):
        task = task_named(tasks, name)
        command = task["ansible.builtin.command"]
        assert "--set=database_password={{ _database_password }}" in command["argv"]
        assert "stdin" in command
        assert "--command" not in " ".join(command["argv"])
        assert "PASSWORD :'database_password'" in command["stdin"]


def postgres_superuser_is_an_explicit_database_request():
    defaults = yaml.safe_load(read("ansible/vars/app-defaults/immich.yml"))[
        "immich_defaults"
    ]
    assert defaults["app"]["database"]["postgresql_superuser"] is True

    tasks = yaml.safe_load(read("ansible/tasks/database/provision.yml"))
    request = task_named(tasks, "Database | Assert provisioning request")
    conditions = request["ansible.builtin.assert"]["that"]
    assert any("postgresql_superuser" in condition and "is boolean" in condition for condition in conditions)
    assert any("provider == 'postgresql'" in condition for condition in conditions)

    status = task_named(tasks, "Database | Read the PostgreSQL role superuser status")
    assert "rolsuper::text" in " ".join(status["ansible.builtin.command"]["argv"])

    create = task_named(tasks, "Database | Create PostgreSQL application role")
    assert "SUPERUSER" in create["ansible.builtin.command"]["stdin"]
    assert "NOSUPERUSER" in create["ansible.builtin.command"]["stdin"]
    assert "_database_role_status.stdout | trim == 'missing'" in create["when"]

    reconcile = task_named(
        tasks, "Database | Reconcile PostgreSQL application role superuser status"
    )
    alter = " ".join(reconcile["ansible.builtin.command"]["argv"])
    assert "ALTER ROLE" in alter and "SUPERUSER" in alter and "NOSUPERUSER" in alter
    assert any("_database_role_status.stdout | trim != 'missing'" in condition for condition in reconcile["when"])
    assert any("_database_role_status.stdout | trim == 'true'" in condition for condition in reconcile["when"])


def immich_database_extensions_are_prepared_by_the_backend():
    defaults = yaml.safe_load(read("ansible/vars/app-defaults/immich.yml"))[
        "immich_defaults"
    ]
    extensions = defaults["app"]["database"]["extensions"]
    assert extensions == ["vector", "earthdistance"], extensions

    tasks = yaml.safe_load(read("ansible/tasks/database/provision.yml"))
    request = task_named(tasks, "Database | Assert provisioning request")
    allowed = next(
        condition
        for condition in request["ansible.builtin.assert"]["that"]
        if "difference" in condition
    )
    assert "vector" in allowed and "earthdistance" in allowed, allowed

    check = task_named(
        tasks, "Database | Check whether the PostgreSQL earthdistance extension exists"
    )
    assert "earthdistance" in " ".join(check["ansible.builtin.command"]["argv"])
    enable = task_named(tasks, "Database | Enable the PostgreSQL earthdistance extension")
    command = enable["ansible.builtin.command"]["argv"]
    assert any("CREATE EXTENSION IF NOT EXISTS earthdistance CASCADE" in arg for arg in command)
    assert enable["delegate_to"] == "database_backend_target"
    assert enable["become_user"] == "postgres"


def postgresql_delete_and_redeploy_reconcile_cluster_identity():
    remove_plays = yaml.safe_load(read("ansible/playbooks/apps/remove.yml"))
    native_play = next(play for play in remove_plays if play["name"].startswith("Remove App | Stop"))
    native_tasks = native_play["tasks"]
    native_block = task_named(native_tasks, "Stop the native service")["block"]
    resolve = task_named(native_play["pre_tasks"], "Resolve every PostgreSQL cluster removal unit")
    drop = task_named(native_block, "Drop PostgreSQL clusters with their configuration when deleting data")
    assert drop["ansible.builtin.command"]["argv"] == [
        "pg_dropcluster",
        "--stop",
        "{{ _rm_postgresql_cluster[0] }}",
        "{{ _rm_postgresql_cluster[1] }}",
    ]
    assert "_rm_delete_data | bool" in drop["when"]
    assert "^[0-9]+[ ]+" in resolve["loop"] and "^[0-9]+[ ]+" in drop["loop"]
    assert native_block.index(drop) < native_block.index(task_named(native_block, "Delete the data path"))

    postgres_tasks = yaml.safe_load(read("ansible/roles/postgresql/tasks/main.yml"))
    discover = task_named(postgres_tasks, "PostgreSQL | Discover registered clusters after package installation")
    create = task_named(postgres_tasks, "PostgreSQL | Create main when no cluster is registered")
    stale_guard = task_named(postgres_tasks, "PostgreSQL | Refuse registered clusters with missing data")
    identity_file = task_named(postgres_tasks, "PostgreSQL | Check the existing main cluster identity file")
    assert discover["ansible.builtin.command"]["argv"] == ["pg_lsclusters", "--no-header"]
    assert create["ansible.builtin.command"]["argv"] == [
        "pg_createcluster",
        "{{ app_config.app.version }}",
        "main",
    ]
    assert create["when"] == "_postgresql_main_clusters | length == 0"
    assert identity_file["ansible.builtin.stat"]["path"] == "{{ _postgresql_main_data_directory }}/PG_VERSION"
    assert "_postgresql_main_version_file.stat.isreg" in stale_guard["ansible.builtin.assert"]["that"]
    assert "delete_data=true" in stale_guard["ansible.builtin.assert"]["fail_msg"]
    assert postgres_tasks.index(discover) < postgres_tasks.index(create)
    assert postgres_tasks.index(stale_guard) < postgres_tasks.index(create)


if __name__ == "__main__":
    database_producers_resolve_instance_keys()
    database_writer_removes_unresolved_keys_and_preserves_other_estates()
    database_role_password_sql_uses_psql_stdin()
    postgres_superuser_is_an_explicit_database_request()
    immich_database_extensions_are_prepared_by_the_backend()
    postgresql_delete_and_redeploy_reconcile_cluster_identity()
    print("ok database fact producers and registry cleanup")
