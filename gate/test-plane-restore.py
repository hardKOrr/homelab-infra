#!/usr/bin/env python3
"""Check Plane's restore-swap inputs and its Redis and key hooks.

gate/test-restore-swap.py proves the swap and database. Plane's Redis backend is its own
guest, so the replaced hook swaps the RDB there and the rollback hook puts the live RDB
back. The hooks run here on local files with the remote host, services, Redis ping and
Vaultwarden stubbed.
"""

import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import repo, run_hook, swap_inputs

compose = (repo / "ansible/roles/plane/templates/docker-compose.yml.j2").read_text()
restore = yaml.safe_load((repo / "ansible/roles/plane/tasks/restore.yml").read_text())
deploy = yaml.safe_load((repo / "ansible/roles/plane/tasks/main.yml").read_text())


def check_swap_inputs():
    swap = swap_inputs("plane")
    assert swap["recovery_swap_archives"] == [
        "database.pxar", "redis.pxar", "data.pxar", "object-storage.pxar"]
    assert swap["recovery_swap_database"] == {
        "config": "{{ app_config.app.database }}", "dump": "database/plane.dump"}
    assert swap["recovery_swap_staging_parent"] == "{{ plane_project_dir }}"
    paths = {p["source"]: p for p in swap["recovery_swap_paths"]}
    created = next(t for t in deploy if t.get("name") == "Plane | Create persistent service directories")
    for directory in created["loop"][1:-1]:
        name = directory.rsplit("/", 1)[1]
        assert paths[f"data/{name}"]["path"] == "{{ _plane_restore_data_path }}/" + name, name
        assert (paths[f"data/{name}"]["owner"], paths[f"data/{name}"]["group"]) == ("root", "root")
    assert (paths["data/rabbitmq"]["owner"], paths["data/rabbitmq"]["group"]) == ("live", "live")
    assert paths["object-storage"]["path"] == "{{ _plane_restore_object_storage_path }}"
    bind_mounts = {line.split('"')[1].split(":")[0].replace("{{ app_config.app.data_path }}/", "data/")
                   for line in compose.splitlines() if "app_config.app.data_path }}/" in line}
    assert bind_mounts <= set(paths), f"every data bind mount is restored: {bind_mounts - set(paths)}"
    services = [line.strip().rstrip(":") for line in compose.splitlines()
                if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":")]
    assert swap["recovery_swap_stop_services"] == services, "the swap stops the whole project"
    assert swap["recovery_swap_health_headers"]["Host"].endswith("{{ homelabinfra_infra.domain }}")
    assert swap["recovery_swap_health_follow_redirects"] == "none"

    held = next(t for t in restore if t.get("name") == "Plane restore | Preview or replace under the lock")
    lock = "/run/lock/homelab-infra-plane-{{ restore_target }}.lock"
    assert held["always"][0]["ansible.builtin.file"] == {"path": lock, "state": "absent"}
    assert held["always"][0]["when"] == "_plane_restore_lock is changed"
    guest = (repo / "ansible/roles/plane/files/plane-recovery").read_text()
    assert 'lock="/run/lock/homelab-infra-plane-${INSTANCE}.lock"' in guest


def check_staged_hook():
    def stub(tasks):
        for task in tasks:
            if "redis-check-rdb" in str(task):
                task.pop("ansible.builtin.command")
                task["ansible.builtin.debug"] = {"msg": "redis-check-rdb"}
        return tasks

    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        (stage / "database").mkdir()
        (stage / "redis").mkdir()
        variables = {"recovery_swap_stage": str(stage), "app_config": {"app": {"release": "v1.2.3"}}}
        assert run_hook("plane", "staged", variables, stub).returncode != 0, "no release identity"
        (stage / "database/plane-release.txt").write_text("v1.2.3\n")
        assert run_hook("plane", "staged", variables, stub).returncode != 0, "no Redis RDB"
        (stage / "redis/dump.rdb").write_text("REDIS0011")
        result = run_hook("plane", "staged", variables, stub)
        assert result.returncode == 0, result.stdout + result.stderr
        (stage / "database/plane-release.txt").write_text("v1.2.2\n")
        assert run_hook("plane", "staged", variables, stub).returncode != 0, "another release"


def stub_remote(log):
    """Run delegated tasks locally and log services, pings and key writes."""
    def stub(tasks):
        for task in tasks:
            for inner in [task, *task.get("block", [])]:
                inner.pop("delegate_to", None)
                inner.pop("no_log", None)
                if "ansible.builtin.add_host" in inner:
                    inner.pop("ansible.builtin.add_host")
                    inner["ansible.builtin.debug"] = {"msg": "redis host"}
                if "ansible.builtin.systemd_service" in inner:
                    state = inner.pop("ansible.builtin.systemd_service")["state"]
                    inner["ansible.builtin.shell"] = f"echo redis-{state} >> {log}"
                for module in ("ansible.builtin.copy", "ansible.builtin.fetch"):
                    for key in ("owner", "group", "flat"):
                        inner.get(module, {}).pop(key, None)
                if "ansible.builtin.fetch" in inner:
                    fetch = inner.pop("ansible.builtin.fetch")
                    inner["ansible.builtin.copy"] = {"src": fetch["src"], "dest": fetch["dest"],
                                                     "remote_src": True}
                include = str(inner.get("ansible.builtin.include_tasks", ""))
                if include:
                    # The include keeps its vars, so the keys logged are the ones it passes.
                    inner.pop("ansible.builtin.include_tasks")
                    what = "ping" if "redis-ready" in include else "keys {{ _plane_restore_keys.secret_key }}"
                    inner["ansible.builtin.shell"] = f"echo '{what}' >> {log}"
        return tasks
    return stub


def check_redis_hooks():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "stage/redis").mkdir(parents=True)
        (root / "stage/redis/dump.rdb").write_text("restored rdb")
        (root / "redis").mkdir()
        (root / "redis/dump.rdb").write_text("live rdb")
        (root / "controller").mkdir()
        log = root / "events.log"
        variables = {
            "recovery_swap_stage": str(root / "stage"),
            "_plane_restore_controller": {"path": str(root / "controller")},
            "_plane_restore_redis_backend": {"host": "redis.invalid"},
            "_plane_restore_redis_data_path": str(root / "redis"),
            "_plane_restore_redis_service_name": "redis-server",
            "restore_source_plane_secrets": {"secret_key": "source"},
            "_plane_restore_target_secrets": {"secret_key": "target"},
        }
        result = run_hook("plane", "replaced", variables, stub_remote(log))
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "redis/dump.rdb").read_text() == "restored rdb"
        assert (root / "controller/live.rdb").read_text() == "live rdb"
        assert log.read_text().splitlines() == ["redis-stopped", "redis-started", "ping", "keys source"]

        log.unlink()
        result = run_hook("plane", "rollback", variables | {
            "_plane_restore_redis_replaced": True, "_plane_restore_keys_replaced": True,
            "_plane_restore_live_rdb": {"stat": {"exists": True}}}, stub_remote(log))
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "redis/dump.rdb").read_text() == "live rdb"
        assert log.read_text().splitlines() == ["redis-stopped", "redis-started", "ping", "keys target"]

        # A Redis that had no RDB gets none back; a hook that never replaced anything is a no-op.
        log.unlink()
        result = run_hook("plane", "rollback", variables | {
            "_plane_restore_redis_replaced": True,
            "_plane_restore_live_rdb": {"stat": {"exists": False}}}, stub_remote(log))
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (root / "redis/dump.rdb").exists()
        log.unlink()
        assert run_hook("plane", "rollback", variables, stub_remote(log)).returncode == 0
        assert not log.exists()


if __name__ == "__main__":
    check_swap_inputs()
    check_staged_hook()
    check_redis_hooks()
    print("ok Plane restore: swap inputs, lock, staged release check, Redis and key hooks")
