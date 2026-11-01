#!/usr/bin/env python3
"""Check database memory ratios, guest types and per-instance overrides.

Checks: database memory ratios, LXC/VM guest inputs, per-instance overrides and rendered or
managed engine settings.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
GUEST_MEMORY = ROOT / "ansible/tasks/database/resolve-guest-memory.yml"
SPECS = {
    "postgresql": {
        "resolve": ROOT / "ansible/roles/postgresql/tasks/resolve-memory.yml",
        "low": {
            "_postgresql_shared_buffers_mib": 256,
            "_postgresql_effective_cache_size_mib": 768,
            "_postgresql_work_mem_mib": 1,
        },
        "high": {
            "_postgresql_shared_buffers_mib": 1024,
            "_postgresql_effective_cache_size_mib": 3072,
            "_postgresql_work_mem_mib": 5,
        },
        "override": {
            "shared_buffers": 777,
            "effective_cache_size": 555,
            "work_mem": 11,
        },
        "overridden": {
            "_postgresql_shared_buffers_mib": 777,
            "_postgresql_effective_cache_size_mib": 555,
            "_postgresql_work_mem_mib": 11,
        },
    },
    "redis": {
        "resolve": ROOT / "ansible/roles/redis/tasks/resolve-memory.yml",
        "low": {"_redis_maxmemory_mib": 512, "_redis_maxmemory_policy": "noeviction"},
        "high": {"_redis_maxmemory_mib": 2048, "_redis_maxmemory_policy": "noeviction"},
        "override": {"maxmemory": 321, "maxmemory_policy": "volatile-lru"},
        "overridden": {"_redis_maxmemory_mib": 321, "_redis_maxmemory_policy": "volatile-lru"},
    },
    "mariadb": {
        "resolve": ROOT / "ansible/roles/mariadb/tasks/resolve-memory.yml",
        "low": {"_mariadb_innodb_buffer_pool_size_mib": 512},
        "high": {"_mariadb_innodb_buffer_pool_size_mib": 2048},
        "override": {"innodb_buffer_pool_size": 123},
        "overridden": {"_mariadb_innodb_buffer_pool_size_mib": 123},
    },
    "mysql": {
        "resolve": ROOT / "ansible/roles/mysql/tasks/resolve-memory.yml",
        "low": {"_mysql_innodb_buffer_pool_size_mib": 512},
        "high": {"_mysql_innodb_buffer_pool_size_mib": 2048},
        "override": {"innodb_buffer_pool_size": 123},
        "overridden": {"_mysql_innodb_buffer_pool_size_mib": 123},
    },
    "influxdb": {
        "resolve": ROOT / "ansible/roles/influxdb/tasks/resolve-memory.yml",
        "low": {"_influxdb_cache_max_memory_mib": 32, "_influxdb_cache_snapshot_memory_mib": 1},
        "high": {"_influxdb_cache_max_memory_mib": 128, "_influxdb_cache_snapshot_memory_mib": 3},
        "override": {
            "storage_cache_max_memory_size_mib": 123,
            "storage_cache_snapshot_memory_size_mib": 7,
        },
        "overridden": {
            "_influxdb_cache_max_memory_mib": 123,
            "_influxdb_cache_snapshot_memory_mib": 7,
        },
    },
}


def assert_expressions(values):
    return [
        f"{name} | int == {value}"
        if isinstance(value, int)
        else f"{name} == {value!r}"
        for name, value in values.items()
    ]


def play(
    name,
    engine,
    guest_type,
    memory_config,
    app_memory,
    expected_guest_memory,
    expected,
):
    proxmox = {"guest_type": guest_type}
    proxmox.update(memory_config)
    assertions = ["_database_backend_memory_mib | int == _expected_guest_memory | int"]
    assertions.extend(assert_expressions(expected))
    return {
        "name": name,
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance": f"memory-{engine}",
            "_database_backend_guest_type": guest_type,
            "_expected_guest_memory": expected_guest_memory,
            "app_config": {
                "proxmox": proxmox,
                "app": {"memory": app_memory},
            },
        },
        "tasks": [
            {"ansible.builtin.import_tasks": str(GUEST_MEMORY)},
            {"ansible.builtin.import_tasks": str(SPECS[engine]["resolve"])},
            {"ansible.builtin.assert": {"that": assertions, "fail_msg": name}},
        ],
    }


def check_engine_memory():
    plays = []
    for engine, spec in SPECS.items():
        plays.append(
            play(
                f"{engine} LXC defaults",
                engine,
                "lxc",
                {"memory": 1024},
                {},
                1024,
                spec["low"],
            )
        )
        plays.append(
            play(
                f"{engine} VM defaults",
                engine,
                "vm",
                {"vm": {"memory": 4096}},
                {},
                4096,
                spec["high"],
            )
        )
        plays.append(
            play(
                f"{engine} instance overrides",
                engine,
                "vm",
                {"memory": 4096},
                spec["override"],
                4096,
                spec["overridden"],
            )
        )

        for guest_type, selected_memory, expected in (
            ("lxc", 1024, spec["low"]),
            ("vm", 4096, spec["high"]),
        ):
            plays.append(
                play(
                    f"{engine} {guest_type.upper()} selection with both blocks",
                    engine,
                    guest_type,
                    {
                        "memory": 8192,
                        "lxc": {"memory": 1024},
                        "vm": {"memory": 4096},
                    },
                    # A conflicting shared value must not hide the selected guest block.
                    {},
                    selected_memory,
                    expected,
                )
            )

    for invalid_memory in ("invalid", ["invalid"]):
        plays.append({
            "name": "invalid engine memory is refused before provisioning",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {
                "instance": "memory-invalid",
                "_database_backend_guest_type": "lxc",
                "app_config": {"proxmox": {"memory": 512}, "app": {"memory": invalid_memory}},
            },
            "tasks": [{
                "block": [
                    {"ansible.builtin.include_tasks": str(
                        ROOT / "ansible/tasks/database/provision-backend-guest.yml"
                    )},
                    {"ansible.builtin.fail": {"msg": "Invalid memory reached guest provisioning"}},
                ],
                "rescue": [{"ansible.builtin.assert": {"that":
                    "ansible_failed_task.name == 'Database backend | Validate engine memory inputs'"
                }}],
            }],
        })

    with tempfile.TemporaryDirectory() as directory:
        playbook = Path(directory) / "database-memory.yml"
        playbook.write_text(yaml.safe_dump(plays, sort_keys=False), encoding="utf-8")
        result = subprocess.run(
            [str(ANSIBLE), "-i", "localhost,", str(playbook)],
            text=True,
            capture_output=True,
            env=os.environ
            | {
                "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
                "ANSIBLE_STDOUT_CALLBACK": "default",
                "ANSIBLE_NOCOLOR": "1",
            },
            check=False,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)


def check_config_wiring():
    provision_path = ROOT / "ansible/tasks/proxmox/provision-native-guest.yml"
    provision_text = provision_path.read_text(encoding="utf-8")
    assert "ansible.builtin.import_tasks: resolve-native-memory.yml" in provision_text
    assert provision_text.count("'memory': _native_guest_memory_mib | int") == 2
    assert provision_text.count(
        '_database_backend_guest_type: "{{ _native_guest_guest_type }}"'
    ) == 2

    for engine in ("postgresql", "redis", "mariadb", "mysql", "influxdb"):
        app_playbook = ROOT / f"ansible/playbooks/apps/{engine}.yml"
        assert (
            '_database_backend_guest_type: "{{ _database_backend_guest_type }}"'
            in app_playbook.read_text(encoding="utf-8")
        )

    postgresql_tasks = yaml.safe_load(
        (ROOT / "ansible/roles/postgresql/tasks/main.yml").read_text(encoding="utf-8")
    )
    postgres_lines = [
        task["ansible.builtin.lineinfile"]["line"]
        for task in postgresql_tasks
        if task.get("ansible.builtin.lineinfile", {}).get("path") == "{{ _postgresql_conf }}"
    ]
    assert "shared_buffers = {{ _postgresql_shared_buffers_mib }}MB" in postgres_lines
    assert "effective_cache_size = {{ _postgresql_effective_cache_size_mib }}MB" in postgres_lines
    assert "work_mem = {{ _postgresql_work_mem_mib }}MB" in postgres_lines

    for engine, template, values in (
        (
            "redis",
            "ansible/roles/redis/templates/redis.conf.j2",
            {"app_config": {"app": {"port": 6379, "data_path": "/var/lib/redis"}},
             "ansible_default_ipv4": {"address": "192.0.2.1"},
             "_redis_maxmemory_mib": 512, "_redis_maxmemory_policy": "noeviction"},
        ),
        (
            "mariadb",
            "ansible/roles/mariadb/templates/homelab-infra.cnf.j2",
            {"_mariadb_innodb_buffer_pool_size_mib": 512},
        ),
        (
            "mysql",
            "ansible/roles/mysql/templates/homelab-infra.cnf.j2",
            {"_mysql_innodb_buffer_pool_size_mib": 512},
        ),
    ):
        template_path = ROOT / template
        environment = Environment(
            loader=FileSystemLoader(str(template_path.parent)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        rendered = environment.get_template(template_path.name).render(**values)
        if engine == "redis":
            assert "maxmemory 512mb" in rendered
            assert "maxmemory-policy noeviction" in rendered
        else:
            assert "innodb_buffer_pool_size = 512M" in rendered

    influx_tasks = yaml.safe_load(
        (ROOT / "ansible/roles/influxdb/tasks/main.yml").read_text(encoding="utf-8")
    )
    influx_lines = [
        task["ansible.builtin.lineinfile"]["line"]
        for task in influx_tasks
        if task.get("ansible.builtin.lineinfile", {}).get("path") == "/etc/default/influxdb2"
    ]
    assert any("INFLUXD_STORAGE_CACHE_MAX_MEMORY_SIZE=" in line for line in influx_lines)
    assert any("INFLUXD_STORAGE_CACHE_SNAPSHOT_MEMORY_SIZE=" in line for line in influx_lines)


def main():
    check_engine_memory()
    check_config_wiring()
    print("ok database memory ratios, LXC/VM inputs, overrides and config wiring")


if __name__ == "__main__":
    main()
