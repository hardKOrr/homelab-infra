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
    source_map = {
        "postgresql-keep": {"provider": "postgresql"},
        "redis-keep": {"provider": "redis"},
    }
    roots = {
        "default": {
            "domain": "example.test",
            "databases": {"{{ instance }}": {"provider": "redis"}, "other": {"provider": "mysql"}},
            "estates": {"foxglove": {"databases": {"mysql-keep": {"provider": "mysql"}}}},
        },
        "estate": {
            "databases": {"postgresql-default": {"provider": "postgresql"}},
            "estates": {
                "foxglove": {
                    "domain": "foxglove.example.test",
                    "databases": {"{{ instance }}": {"provider": "redis"}, "old": {"provider": "mysql"}},
                },
                "other": {"databases": {"mysql-other": {"provider": "mysql"}}},
            },
        },
    }
    for scope, root in roots.items():
        estate = "" if scope == "default" else "foxglove"
        expected = (
            {**root, "databases": source_map}
            if not estate
            else {
                **root,
                "estates": {
                    **root["estates"],
                    estate: {**root["estates"][estate], "databases": source_map},
                },
            }
        )
        for root_variable in ("_gf_existing_infra", "_gf_existing_local_infra"):
            expression = memory_task["ansible.builtin.set_fact"][root_variable]
            actual = render(
                expression,
                {
                    root_variable: root,
                    "_gf_clean_database_registry": source_map,
                    "_generated_facts_estate": estate,
                },
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


if __name__ == "__main__":
    database_producers_resolve_instance_keys()
    database_writer_removes_unresolved_keys_and_preserves_other_estates()
    database_role_password_sql_uses_psql_stdin()
    immich_database_extensions_are_prepared_by_the_backend()
    print("ok database fact producers and registry cleanup")
