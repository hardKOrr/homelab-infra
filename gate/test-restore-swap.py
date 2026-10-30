#!/usr/bin/env python3
"""Exercise the shared restore swap (ansible/tasks/recovery/restore-swap.yml) on fixtures.

restore-swap.yml and scripts/restore-swap.sh run unchanged on local directories, except:
- the Compose and systemd service tasks are rewritten to record `stop`/`start` calls;
- pbs-client-restore.yml is a fake that records the preview and copies a fixture recovery
  point into the staging paths (gate/test-pbs-client.py proves the real one);
- the database tasks are fakes that keep the "database" in one file and log each verify,
  dump and restore into the hook log (gate/test-database-recovery.py proves the real ones);
- fake `setpriv`, `findmnt` and `chown` on PATH record their calls, so a mount can be a
  plain directory and the run needs no root.

The cases prove: the preview changes nothing; a swap installs the point with the declared
owner and mode and leaves no rollback copy or staging; a failure after the stop puts every
original back byte for byte and starts it; a staged-data failure never stops the
application; an earlier run's rollback copy refuses the restore; a mount swaps its
contents as its owner; an incomplete rollback keeps the services stopped and the copies.
With a database: its dump and ownership are checked before the stop, the live database is dumped after
it, the archived dump loads before the replaced hook, and a failure once that load started
loads the live dump back; a failed database rollback keeps the services stopped.
"""

import grp
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
uid, gid = os.getuid(), os.getgid()
user, group = pwd.getpwuid(uid).pw_name, grp.getgrgid(gid).gr_name

SERVICE_MODULES = ("community.docker.docker_compose_v2", "ansible.builtin.systemd_service")

FAKE_RESTORE = """
- name: Fake PBS restore | Record the request
  ansible.builtin.shell: >-
    echo {{ {'overwrite': pbs_client_overwrite | default(restore_overwrite) | bool,
             'archives': pbs_client_archives, 'args': pbs_client_restore_args | default([])}
           | to_json | quote }} >> {{ test_root }}/client.log
- name: Fake PBS restore | Extract the fixture point
  ansible.builtin.command:
    argv: [cp, -a, "{{ test_root }}/point/{{ item.path | basename }}/.", "{{ item.path }}/"]
  loop: "{{ pbs_client_archives if pbs_client_overwrite | default(restore_overwrite) | bool else [] }}"
"""

# Each fake logs `db-<action> <file>` and checks it got the swap's instance and config.
FAKE_DATABASE = {
    "verify-dump": """
- ansible.builtin.assert:
    that: [database_app_instance == 'gate-app', database_app_config.name == 'gate']
- ansible.builtin.shell: echo db-verify {{ database_restore_file | basename }} >> {{ test_root }}/hooks.log
- ansible.builtin.command: grep -qx 'archived database' {{ database_restore_file }}
""",
    "dump": """
- ansible.builtin.shell: echo db-dump {{ database_dump_file | basename }} >> {{ test_root }}/hooks.log
- ansible.builtin.copy: {src: "{{ test_root }}/database", dest: "{{ database_dump_file }}", remote_src: true}
""",
    "guard-app-database": """
- ansible.builtin.shell: echo db-guard >> {{ test_root }}/hooks.log
- ansible.builtin.fail: {msg: another application's item uses the database}
  when: fail_guard | default(false) | bool
""",
    # A load replaces the contents even when it then fails, as a partial MariaDB load would.
    "restore": """
- ansible.builtin.shell: echo db-restore {{ database_restore_file | basename }} >> {{ test_root }}/hooks.log
- ansible.builtin.copy: {src: "{{ database_restore_file }}", dest: "{{ test_root }}/database", remote_src: true}
- ansible.builtin.fail: {msg: database load failed}
  when: fail_database | default('') == database_restore_file | basename
""",
}

FAKES = {
    # Drop the identity options and run the command as the current user.
    "setpriv": """#!/usr/bin/env bash
echo "$PWD $*" >> "$TEST_ROOT/setpriv.log"
while [ "$1" != -- ]; do shift; done; shift; exec "$@"
""",
    "findmnt": """#!/usr/bin/env bash
while [ $# -gt 0 ]; do [ "$1" = --mountpoint ] && path=$2; shift; done
grep -qxF "$path" "$TEST_ROOT/mounts" && echo "$path"
""",
    "chown": """#!/usr/bin/env bash
echo "$*" >> "$TEST_ROOT/chown.log"
exec /bin/chown "$@"
""",
}


def stub_services(tasks):
    for task in tasks or []:
        for module in SERVICE_MODULES:
            if module in task:
                state = "stop" if task.pop(module)["state"] == "stopped" else "start"
                names = ("{{ _swap_services | join(',') }}" if module.startswith("community")
                         else "{{ item }}")
                task["ansible.builtin.shell"] = f"echo {state} {names} >> {{{{ test_root }}}}/services.log"
        for section in ("block", "rescue", "always"):
            stub_services(task.get(section))


def build(root):
    tasks = root / "ansible/tasks"
    shutil.copytree(repo / "ansible/tasks/recovery", tasks / "recovery")
    swap = yaml.safe_load((tasks / "recovery/restore-swap.yml").read_text())
    stub_services(swap)
    (tasks / "recovery/restore-swap.yml").write_text(yaml.safe_dump(swap, sort_keys=False))
    (tasks / "backup").mkdir()
    (tasks / "backup/pbs-client-restore.yml").write_text(FAKE_RESTORE)
    (tasks / "database").mkdir()
    for name, body in FAKE_DATABASE.items():
        (tasks / f"database/{name}.yml").write_text(body)
    (root / "bin").mkdir()
    for name, body in FAKES.items():
        (root / "bin" / name).write_text(body)
        (root / "bin" / name).chmod(0o755)
    hooks = root / "hooks"
    hooks.mkdir()
    for name in ("staged", "stopped", "replaced", "verified", "rollback"):
        (hooks / f"{name}.yml").write_text(
            f"- ansible.builtin.shell: echo {name} >> {{{{ test_root }}}}/hooks.log\n"
            f"- ansible.builtin.fail: {{msg: '{name} hook failed'}}\n"
            f"  when: fail_hook | default('') == '{name}'\n")


def write_tree(base, files):
    shutil.rmtree(base, ignore_errors=True)
    for name, text in files.items():
        (base / name).parent.mkdir(parents=True, exist_ok=True)
        (base / name).write_text(text)


def tree(base):
    return {str(p.relative_to(base)): p.read_text() for p in sorted(base.rglob("*")) if p.is_file()}


def reset(root, mounts=()):
    for name in ("client.log", "services.log", "hooks.log", "setpriv.log", "chown.log", "mounts",
                 "dump-mode"):
        (root / name).unlink(missing_ok=True)
    (root / "mounts").write_text("".join(f"{m}\n" for m in mounts))
    (root / "database").write_text("live database\n")
    write_tree(root / "live", {
        "app/data/db.sqlite": "live db", "app/data/sub/keep": "live sub",
        "app/config/app.ini": "live ini", "media/photo.jpg": "live photo",
    })
    write_tree(root / "point", {
        "data/app/db.sqlite": "restored db", "data/app/new": "restored new",
        "data/config/app.ini": "restored ini",
        "media/photo.jpg": "restored photo", "media/album/two.jpg": "restored two",
        "data/database/gate.dump": "archived database\n",
    })


def run(root, paths, overwrite=True, **extra):
    variables = {
        "recovery_swap_runtime": "docker",
        "recovery_swap_compose_project": str(root / "live/app"),
        "recovery_swap_stop_services": ["app", "worker"],
        "recovery_swap_archives": sorted({"data.pxar"} | set(extra.pop("archives", []))),
        "recovery_swap_paths": paths,
        **{f"recovery_swap_{h}_hook": str(root / f"hooks/{h}.yml")
           for h in ("staged", "stopped", "replaced", "verified", "rollback")},
        **extra,
    }
    playbook = root / "play.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {"instance": "gate-app", "test_root": str(root), "restore_overwrite": overwrite},
        "tasks": [
            {"ansible.builtin.include_tasks": str(root / "ansible/tasks/recovery/restore-swap.yml"),
             "vars": variables},
            {"ansible.builtin.shell": "echo {{ _application_restore_complete }} > " + str(root / "complete")},
        ],
    }]))
    env = os.environ | {
        "PATH": f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "TEST_ROOT": str(root),
        "ANSIBLE_NOCOLOR": "1",
    }
    (root / "complete").unlink(missing_ok=True)
    return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                          cwd=root, env=env, capture_output=True, text=True)


def lines(root, name):
    path = root / name
    return path.read_text().splitlines() if path.exists() else []


def leftovers(root):
    return sorted(str(p.relative_to(root / "live")) for p in (root / "live").rglob("*restore*"))


def expect(result, ok, case):
    if (result.returncode == 0) != ok:
        sys.stderr.write(result.stdout[-6000:] + result.stderr[-3000:])
        raise AssertionError(f"{case}: expected {'success' if ok else 'failure'}")


def main():
    with tempfile.TemporaryDirectory(prefix="gate-restore-swap-") as tmp:
        root = Path(tmp)
        build(root)
        live = root / "live"
        data = {"path": str(live / "app/data"), "source": "data/app", "owner": user,
                "group": group, "mode": "0750"}
        config = {"path": str(live / "app/config"), "source": "data/config",
                  "owner": uid, "group": gid, "mode": "0700"}
        media = {"path": str(live / "media"), "source": "media", "owner": uid, "group": gid,
                 "mode": "0770", "mount": True}

        # Preview: the client previews, nothing else runs.
        reset(root)
        before = tree(live)
        expect(run(root, [data, config], overwrite=False), True, "preview")
        assert tree(live) == before and not lines(root, "services.log")
        assert [json.loads(c)["overwrite"] for c in lines(root, "client.log")] == [False]
        assert not lines(root, "hooks.log") and leftovers(root) == []

        # Success: both paths replaced in hook order, owner by numeric id, no leftovers.
        reset(root)
        expect(run(root, [data, config]), True, "swap")
        assert tree(live / "app/data") == {"db.sqlite": "restored db", "new": "restored new"}
        assert tree(live / "app/config") == {"app.ini": "restored ini"}
        assert lines(root, "hooks.log") == ["staged", "stopped", "replaced", "verified"]
        assert lines(root, "services.log") == ["stop app,worker", "start app,worker"]
        assert (live / "app/data").stat().st_mode & 0o7777 == 0o750
        assert (live / "app/config").stat().st_mode & 0o7777 == 0o700
        owner = f"{uid}:{gid}"
        assert f"-R --no-dereference {owner} {live}/app/data" in lines(root, "chown.log")
        assert f"-R --no-dereference {owner} {live}/app/config" in lines(root, "chown.log")
        requests = [json.loads(c) for c in lines(root, "client.log")]
        assert [r["overwrite"] for r in requests] == [False, True]
        args = requests[1]["args"]
        for option in ("--ignore-ownership", "--ignore-acls", "--ignore-xattrs"):
            assert args[args.index(option) + 1] == "true", option
        assert "--ignore-permissions" not in args
        assert leftovers(root) == [] and not list(live.glob(".gate-app-restore-*"))
        assert (root / "complete").read_text().strip() == "True"

        # Failure after the stop: every original back byte for byte, then started.
        for hook in ("stopped", "replaced", "verified"):
            reset(root)
            before = tree(live)
            result = run(root, [data, config], fail_hook=hook)
            expect(result, False, f"{hook} failure")
            assert tree(live) == before, hook
            started = ["start app,worker"] if hook == "verified" else []
            assert lines(root, "services.log") == ["stop app,worker", *started, "stop app,worker",
                                                   "start app,worker"], hook
            assert lines(root, "hooks.log")[-1] == "rollback", hook
            assert "The original state is back" in result.stdout, hook
            assert leftovers(root) == [] and not list(live.glob(".gate-app-restore-*")), hook

        # Staged data rejected: the application is never stopped.
        reset(root)
        before = tree(live)
        expect(run(root, [data], fail_hook="staged"), False, "staged failure")
        assert tree(live) == before and not lines(root, "services.log")
        assert not list(live.glob(".gate-app-restore-*"))

        # An empty source is refused before the stop unless the path allows it.
        reset(root)
        (root / "point/data/config/app.ini").unlink()
        expect(run(root, [data, config]), False, "empty source")
        assert not lines(root, "services.log")
        reset(root)
        (root / "point/data/config/app.ini").unlink()
        expect(run(root, [data, dict(config, allow_empty=True)]), True, "allowed empty source")
        assert tree(live / "app/config") == {}

        # A missing source in the point is refused before the stop.
        reset(root)
        expect(run(root, [dict(config, source="data/absent")]), False, "missing source")
        assert not lines(root, "services.log")

        # An earlier run's rollback copy refuses the restore before anything stops.
        reset(root)
        (live / "app/.data.restore-previous").mkdir()
        expect(run(root, [data]), False, "leftover rollback copy")
        assert not lines(root, "services.log") and tree(live / "app/data")["db.sqlite"] == "live db"

        # A mount swaps its contents as its owner and keeps the mount point itself.
        reset(root, mounts=[media["path"]])
        expect(run(root, [data, media], archives=["media.pxar"],
                   recovery_swap_ignore_permissions=True), True, "mount swap")
        assert tree(live / "media") == {"photo.jpg": "restored photo", "album/two.jpg": "restored two"}
        assert (live / "media").stat().st_mode & 0o7777 == 0o770
        as_owner = lines(root, "setpriv.log")
        assert as_owner and all(line.startswith(f"{live}/media --reuid {uid} --regid {gid}")
                                for line in as_owner)
        assert any(" cp -a --no-preserve=ownership " in line for line in as_owner)
        assert not any(line.endswith(str(live / "media")) for line in lines(root, "chown.log")), \
            "root never chowns inside the mount"
        args = json.loads(lines(root, "client.log")[1])["args"]
        assert args[args.index("--ignore-permissions") + 1] == "true"
        assert leftovers(root) == []

        # A mount without a declared mode keeps the mode its owner or the operator gave it.
        reset(root, mounts=[media["path"]])
        (live / "media").chmod(0o755)
        keep_mode = {key: value for key, value in media.items() if key != "mode"}
        expect(run(root, [keep_mode], archives=["media.pxar"]), True, "mount keeps its mode")
        assert (live / "media").stat().st_mode & 0o7777 == 0o755
        (live / "media").chmod(0o770)

        # A mount rolls back as its owner too.
        reset(root, mounts=[media["path"]])
        before = tree(live)
        expect(run(root, [data, media], archives=["media.pxar"], fail_hook="verified"),
               False, "mount rollback")
        assert tree(live) == before and leftovers(root) == []

        # A path that is not the declared mount is refused before anything stops.
        reset(root)
        expect(run(root, [media], archives=["media.pxar"]), False, "unmounted media")
        assert not lines(root, "services.log")

        # Rollback incomplete: services stay stopped, the copies and staging are kept.
        reset(root)
        (root / "hooks/fail-rollback.yml").write_text("- ansible.builtin.fail: {msg: no}\n")
        result = run(root, [data, config], fail_hook="verified",
                     recovery_swap_rollback_hook=str(root / "hooks/fail-rollback.yml"))
        expect(result, False, "incomplete rollback")
        assert lines(root, "services.log")[-1] == "stop app,worker"
        assert "Rollback did not complete" in result.stdout
        assert list(live.glob("app/.gate-app-restore-*/restore-swap.sh"))

        # Database: checked while serving, dumped after the stop, loaded before the replaced hook.
        database = {"config": {"name": "gate"}, "dump": "data/database/gate.dump"}
        reset(root)
        expect(run(root, [data, config], recovery_swap_database=database), True, "database swap")
        assert lines(root, "hooks.log") == [
            "db-verify gate.dump", "db-guard", "staged", "db-dump rollback-gate.dump", "stopped",
            "db-restore gate.dump", "replaced", "verified"], lines(root, "hooks.log")
        assert (root / "database").read_text() == "archived database\n"
        assert tree(live / "app/data") == {"db.sqlite": "restored db", "new": "restored new"}
        assert leftovers(root) == [] and not list(live.glob(".gate-app-restore-*"))

        # A failure before the archived dump loads leaves the database alone.
        reset(root)
        expect(run(root, [data], recovery_swap_database=database, fail_hook="stopped"),
               False, "database untouched")
        assert "db-restore gate.dump" not in lines(root, "hooks.log")
        assert (root / "database").read_text() == "live database\n"

        # A failure once the load started (in the load or later) loads the live dump back
        # before the rollback hook, and the original starts.
        for case, extra in {"failed load": {"fail_database": "gate.dump"},
                            "verified failure": {"fail_hook": "verified"}}.items():
            reset(root)
            before = tree(live)
            result = run(root, [data, config], recovery_swap_database=database, **extra)
            expect(result, False, case)
            assert (root / "database").read_text() == "live database\n", case
            assert lines(root, "hooks.log")[-2:] == ["db-restore rollback-gate.dump", "rollback"], case
            assert tree(live) == before and "The original state is back" in result.stdout, case
            assert lines(root, "services.log")[-1] == "start app,worker", case

        # A failed database rollback keeps the services stopped and the live dump.
        reset(root)
        result = run(root, [data], recovery_swap_database=database, fail_hook="verified",
                     fail_database="rollback-gate.dump")
        expect(result, False, "failed database rollback")
        assert lines(root, "services.log")[-1] == "stop app,worker"
        assert "Rollback did not complete" in result.stdout and "rollback-gate.dump" in result.stdout
        assert list(live.glob("app/.gate-app-restore-*/data/database/rollback-gate.dump"))

        # A missing, empty or unloadable dump is refused before the stop.
        for case, text in {"missing dump": None, "empty dump": "", "unloadable dump": "garbage\n"}.items():
            reset(root)
            dump = root / "point/data/database/gate.dump"
            dump.unlink() if text is None else dump.write_text(text)
            expect(run(root, [data], recovery_swap_database=database), False, case)
            assert not lines(root, "services.log"), case
            assert (root / "database").read_text() == "live database\n", case

        # A database another application claims is refused before the stop.
        reset(root)
        expect(run(root, [data], recovery_swap_database=database, fail_guard=True),
               False, "claimed database")
        assert not lines(root, "services.log") and "db-dump rollback-gate.dump" not in lines(root, "hooks.log")
        assert tree(live / "app/data")["db.sqlite"] == "live db"

        # With a mount in the swap, the dump's directory is closed to the mount's owner.
        reset(root, mounts=[media["path"]])
        (root / "hooks/dump-mode.yml").write_text(
            "- ansible.builtin.shell: stat -c %a {{ recovery_swap_stage }}/data/database"
            " > {{ test_root }}/dump-mode\n")
        expect(run(root, [data, media], archives=["media.pxar"], recovery_swap_database=database,
                   recovery_swap_staged_hook=str(root / "hooks/dump-mode.yml")), True, "private dump")
        assert (root / "dump-mode").read_text().strip() == "700"

        # Unsafe requests are refused before anything stops.
        for case, paths in {
            "relative path": [dict(data, path="app/data")],
            "nested paths": [data, dict(config, path=str(live / "app/data/inner"))],
            "source outside the archives": [dict(config, source="other/config")],
            "mount with a nested source": [dict(media, source="media/album")],
            "overlapping sources": [dict(data, source="data"), config],
        }.items():
            reset(root)
            expect(run(root, paths, archives=["media.pxar"]), False, case)
            assert not lines(root, "services.log"), case
        for case, dump in {"dump inside a source": "data/app/gate.dump",
                           "dump outside the archives": "other/gate.dump",
                           "dump at an archive's top": "data"}.items():
            reset(root)
            expect(run(root, [data], recovery_swap_database=dict(database, dump=dump)), False, case)
            assert not lines(root, "services.log"), case

    print("restore swap: preview, swap, rollback, ownership, mount, database and guards passed")


if __name__ == "__main__":
    main()
