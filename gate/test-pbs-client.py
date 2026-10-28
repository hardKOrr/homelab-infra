#!/usr/bin/env python3
"""Exercise the shared PBS client task files against a fake client.

The production files under ansible/tasks/backup/ run unchanged, except that the PBS target
lookup is replaced by fixed facts. Fake `docker`, `unshare`, `mount` and
`proxmox-backup-client` executables on PATH record every call, so the test proves:
- both runtimes put /tmp on a bounded tmpfs and run every client call with its cache there;
- the Docker runtime installs the pinned client build;
- a backup names its point by the chosen UTC time, verifies the archives, keeps two
  points and proves the previous point survived pruning;
- preview with a point runs the client (archive check, index download, catalog read)
  and extracts nothing; overwrite extracts every archive into its staging path;
- a lab without PBS, a point outside the source group and keep_last below 2 fail before
  the client runs;
- every role restore that stops its application before extracting first runs the shared
  preview on every restore, so those failures happen while the application still serves.
"""

import json
import time
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
pins = yaml.safe_load((repo / "ansible/vars/pbs-client.yml").read_text())["pbs_client_defaults"]

FAKE_CLIENT = r'''#!/usr/bin/env python3
import json, os, sys, time
root = os.environ["PBS_TEST_ROOT"]
state_file = os.path.join(root, "pbs.json")
state = json.load(open(state_file)) if os.path.exists(state_file) else {}
args = sys.argv[1:]
home = os.environ.get("HOME", "")
with open(os.path.join(root, "client.log"), "a") as log:
    log.write(json.dumps({"args": args, "home": home,
                          "cache": os.environ.get("XDG_CACHE_HOME", ""),
                          "password": bool(os.environ.get("PBS_PASSWORD"))}) + "\n")
assert os.environ.get("PBS_PASSWORD") and os.environ.get("PBS_REPOSITORY"), "credentials missing"
assert home.startswith(os.path.join(root, "tmp") + "/"), f"HOME {home} is not on the tmpfs"

def point(group, t):
    return group + "/" + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

def find(p):
    group = p.rsplit("/", 1)[0]
    for snap in state.get(group, []):
        if point(group, snap["time"]) == p:
            return snap
    print(f"snapshot {p} does not exist", file=sys.stderr)
    sys.exit(2)

def save():
    json.dump(state, open(state_file, "w"))

if args[:2] == ["snapshot", "list"]:
    snaps = state.get(args[2], [])
    if "json" in args:
        print(json.dumps([{"backup-time": s["time"], "backup-id": args[2]} for s in snaps]))
    else:
        for s in snaps:
            print(point(args[2], s["time"]))
elif args[:2] == ["snapshot", "files"]:
    snap = find(args[2])
    print(json.dumps([{"filename": f} for f in snap["files"]]))
elif args[0] == "backup":
    backup_id = args[args.index("--backup-id") + 1]
    t = int(args[args.index("--backup-time") + 1])
    files = ["index.json.blob", "catalog.pcat1.didx"]
    for spec in args[1:]:
        if spec.startswith("--"):
            break
        name, path = spec.split(":", 1)
        assert os.path.isdir(path), f"{path} missing"
        files.append(name + ".didx")
    group = "host/" + backup_id
    state.setdefault(group, []).append({"time": t, "files": files, "args": args})
    save()
elif args[0] == "prune":
    keep = int(args[args.index("--keep-last") + 1])
    if os.environ.get("PBS_TEST_CASE") == "prune-everything-old":
        keep = 1
    state[args[1]] = sorted(state.get(args[1], []), key=lambda s: s["time"])[-keep:]
    save()
elif args[0] == "restore":
    snap = find(args[1])
    name, dest = args[2], args[3]
    if name == "index.json":
        open(dest, "w").write(json.dumps({"files": snap["files"]}))
    else:
        assert name + ".didx" in snap["files"], f"{name} not in point"
        assert os.path.isdir(dest), f"staging {dest} missing"
        open(os.path.join(dest, "restored-" + name), "w").write(" ".join(args[4:]))
elif args[:2] == ["catalog", "dump"]:
    find(args[2])
    print("d ./")
else:
    print("unexpected client call " + " ".join(args), file=sys.stderr)
    sys.exit(9)
'''

# Runs the container script on this machine. /tmp inside the script becomes the test's
# tmp directory, and the apt install block (which needs root) is checked, then dropped.
FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, re, subprocess, sys
root = os.environ["PBS_TEST_ROOT"]
args = sys.argv[1:]
open(os.path.join(root, "docker.log"), "a").write(json.dumps(args) + "\n")
tmpfs = [args[i + 1] for i, a in enumerate(args) if a == "--tmpfs"]
assert any(t.startswith("/tmp:") and "size=" in t for t in tmpfs), f"no bounded /tmp tmpfs: {tmpfs}"
script = args[args.index("-ceu") + 1]
install = re.search(r"\{\n(.*?)\n\} >&2\n", script, re.S)
assert install and 'proxmox-backup-client=$PBS_CLIENT_VERSION' in install.group(1), "no pinned install"
script = script.replace(install.group(0), "")
script = re.sub(r"(?<![\w/.])/tmp(?=[/\s])", os.path.join(root, "tmp"), script)
sys.exit(subprocess.run(["/bin/sh", "-ceu", script, "pbs-client"]).returncode)
'''

FAKE_UNSHARE = r'''#!/usr/bin/env python3
import json, os, re, subprocess, sys
root = os.environ["PBS_TEST_ROOT"]
args = sys.argv[1:]
open(os.path.join(root, "unshare.log"), "a").write(json.dumps(args) + "\n")
assert args[:5] == ["--mount", "--propagation", "private", "--", "/bin/sh"], args
script = args[args.index("-ceu") + 1]
script = re.sub(r"(?<![\w/.])/tmp(?=[/\s])", os.path.join(root, "tmp"), script)
sys.exit(subprocess.run(["/bin/sh", "-ceu", script, "pbs-client"]).returncode)
'''

FAKE_MOUNT = r'''#!/bin/sh
printf '%s\n' "$*" >> "$PBS_TEST_ROOT/mount.log"
'''

STUB_TARGET = """\
- name: Stub PBS target
  ansible.builtin.set_fact:
    k8s_pbs_available: "{{ gate_pbs_available | default(true) }}"
    k8s_pbs_repository: gate@pbs.example.invalid:store
    k8s_pbs_fingerprint: "00:11"
    k8s_pbs_password: gate-only-secret
"""


def build_tree(root):
    """Copy the production task files beside a stubbed PBS target lookup."""
    tasks = root / "ansible/tasks"
    (tasks / "backup").mkdir(parents=True)
    (root / "ansible/vars").mkdir(parents=True)
    for source in (repo / "ansible/tasks/backup").glob("pbs-client*.yml"):
        shutil.copy(source, tasks / "backup" / source.name)
    shutil.copy(repo / "ansible/tasks/fail-with-scrubbed-output.yml", tasks)
    shutil.copy(repo / "ansible/vars/pbs-client.yml", root / "ansible/vars")
    (tasks / "backup/resolve-pbs-target.yml").write_text(STUB_TARGET)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name, body in [("proxmox-backup-client", FAKE_CLIENT), ("docker", FAKE_DOCKER),
                       ("unshare", FAKE_UNSHARE), ("mount", FAKE_MOUNT)]:
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    for directory in ["tmp", "data/app", "data/cache", "stage/data", "stage/cache"]:
        (root / directory).mkdir(parents=True, exist_ok=True)


def run(root, task_file, variables, case=""):
    playbook = root / "play.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {"instance": "gate-app", "app_config": {"backup": {"retention_days": 7}}},
        "tasks": [{"ansible.builtin.include_tasks": str(root / "ansible/tasks/backup" / task_file),
                   "vars": variables}],
    }]))
    env = os.environ | {
        "PATH": f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "PBS_TEST_ROOT": str(root),
        "PBS_TEST_CASE": case,
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_STDOUT_CALLBACK": "default",
        "ANSIBLE_DISPLAY_OK_HOSTS": "1",
    }
    return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                          cwd=root, env=env, capture_output=True, text=True)


def calls(root):
    log = root / "client.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def reset_logs(root):
    for name in ["client.log", "docker.log", "unshare.log", "mount.log"]:
        (root / name).unlink(missing_ok=True)


def check_roles_verify_before_stop():
    """A role that stops its application before extracting must first verify the point."""
    def walk(tasks):
        for task in tasks or []:
            yield task
            # Only the block runs in order; rescue and always are failure paths.
            yield from walk(task.get("block"))

    def stops(task):
        for module in ["community.docker.docker_compose_v2", "ansible.builtin.systemd_service",
                       "ansible.builtin.systemd"]:
            if (task.get(module) or {}).get("state") == "stopped":
                return True
        return False

    def shared_restore(task):
        return str(task.get("ansible.builtin.include_tasks", "")).endswith("pbs-client-restore.yml")

    checked = 0
    for path in sorted((repo / "ansible/roles").glob("*/tasks/*.yml")):
        if "pbs-client-restore.yml" not in path.read_text():
            continue
        tasks = list(walk(yaml.safe_load(path.read_text())))
        extract = next((i for i, t in enumerate(tasks) if shared_restore(t)
                        and all("path" in a for a in t["vars"]["pbs_client_archives"])), None)
        stop = next((i for i, t in enumerate(tasks) if stops(t)), None)
        if extract is None or stop is None or stop > extract:
            continue
        checked += 1
        assert any(shared_restore(t) and t["vars"].get("pbs_client_overwrite") is False
                   and "when" not in t for t in tasks[:stop]), (
            f"{path.relative_to(repo)} stops its application before the shared PBS restore "
            "checks the point; run the preview (pbs_client_overwrite: false) on every restore first")
    assert checked, "no role restore stops its application before extracting; the check found nothing"


def expect(result, ok, label):
    if (result.returncode == 0) != ok:
        sys.stderr.write(result.stdout + result.stderr)
        raise AssertionError(f"{label}: expected {'success' if ok else 'failure'}")


def main():
    check_roles_verify_before_stop()
    assert pins["suites"]["bookworm"]["version"] and pins["suites"]["trixie"]["version"]
    assert pins["tmpfs_size"]
    # The shared code refuses paths under /tmp, where its own tmpfs goes.
    with tempfile.TemporaryDirectory(dir="/var/tmp") as tmp:
        root = Path(tmp)
        build_tree(root)
        archives = [{"name": "data.pxar", "path": str(root / "data/app")},
                    {"name": "cache.pxar", "path": str(root / "data/cache")}]
        state = root / "pbs.json"

        for runtime in ["native", "docker"]:
            state.unlink(missing_ok=True)
            reset_logs(root)
            backup = {"pbs_client_runtime": runtime, "pbs_client_archives": archives,
                      "pbs_client_backup_args": ["--exclude", "/logs"]}
            for attempt in range(3):
                expect(run(root, "pbs-client-backup.yml", backup), True, f"{runtime} backup {attempt}")
            points = json.loads(state.read_text())["host/gate-app"]
            assert len(points) == 2, f"{runtime}: keep-last 2 should leave two points: {points}"
            backup_call = next(c for c in calls(root) if c["args"][0] == "backup")
            assert "--backup-time" in backup_call["args"] and "/logs" in backup_call["args"]
            prune = next(c for c in calls(root) if c["args"][0] == "prune")["args"]
            assert prune[prune.index("--keep-last") + 1] == "2"
            assert prune[prune.index("--keep-daily") + 1] == "7"
            assert all(c["cache"].startswith(str(root / "tmp") + "/") for c in calls(root))
            if runtime == "native":
                mounts = (root / "mount.log").read_text()
                assert "-t tmpfs" in mounts and "size=" + pins["tmpfs_size"] in mounts, mounts
                assert (root / "unshare.log").exists() and not (root / "docker.log").exists()
            else:
                assert (root / "docker.log").exists() and not (root / "unshare.log").exists()
                docker = json.loads((root / "docker.log").read_text().splitlines()[0])
                assert f"--volume={root / 'data/app'}:{root / 'data/app'}:ro" in docker

            newest = sorted(points, key=lambda p: p["time"])[-1]["time"]
            snapshot = "host/gate-app/" + time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(newest))
            stage = [{"name": "data.pxar", "path": str(root / "stage/data")},
                     {"name": "cache.pxar", "path": str(root / "stage/cache")}]

            reset_logs(root)
            # A preview names the archives only; it has no staging paths.
            preview = {"pbs_client_runtime": runtime,
                       "pbs_client_archives": [{"name": "data.pxar"}, {"name": "cache.pxar"}],
                       "pbs_client_snapshot": snapshot, "pbs_client_overwrite": False}
            expect(run(root, "pbs-client-restore.yml", preview), True, f"{runtime} preview")
            verbs = [" ".join(c["args"][:2]) for c in calls(root)]
            assert "snapshot files" in verbs and "catalog dump" in verbs, verbs
            assert any(c["args"][:3] == ["restore", snapshot, "index.json"] for c in calls(root))
            assert not list((root / "stage/data").iterdir()), "preview extracted data"

            reset_logs(root)
            listing = {"pbs_client_runtime": runtime, "pbs_client_archives": stage,
                       "pbs_client_overwrite": False}
            result = run(root, "pbs-client-restore.yml", listing)
            expect(result, True, f"{runtime} list")
            assert snapshot in result.stdout

            reset_logs(root)
            overwrite = preview | {"pbs_client_overwrite": True, "pbs_client_archives": stage,
                                   "pbs_client_restore_args": ["--ignore-ownership", "true"]}
            expect(run(root, "pbs-client-restore.yml", overwrite), True, f"{runtime} overwrite")
            restored = (root / "stage/data/restored-data.pxar").read_text()
            assert restored == "--ignore-ownership true", restored
            assert (root / "stage/cache/restored-cache.pxar").exists()
            for path in (root / "stage").glob("*/*"):
                path.unlink()

        reset_logs(root)
        foreign = {"pbs_client_runtime": "native", "pbs_client_archives": archives,
                   "pbs_client_snapshot": "host/other-app/2026-01-01T00:00:00Z",
                   "pbs_client_overwrite": False}
        expect(run(root, "pbs-client-restore.yml", foreign), False, "foreign point")
        assert not calls(root), "a point outside the group must fail before the client runs"

        reset_logs(root)
        for task_file in ["pbs-client-backup.yml", "pbs-client-restore.yml"]:
            result = run(root, task_file, {"pbs_client_runtime": "native", "pbs_client_archives": archives,
                                           "gate_pbs_available": False})
            expect(result, False, f"{task_file} without PBS")
            assert "No usable PBS" in result.stdout + result.stderr
        assert not calls(root), "a lab without PBS must fail before the client runs"

        short = {"pbs_client_runtime": "native", "pbs_client_archives": archives,
                 "pbs_client_keep_last": 1}
        expect(run(root, "pbs-client-backup.yml", short), False, "keep_last 1")
        assert not calls(root)

        staged_in_tmp = {"pbs_client_runtime": "native",
                         "pbs_client_archives": [{"name": "data.pxar", "path": "/tmp/stage"}]}
        expect(run(root, "pbs-client-backup.yml", staged_in_tmp), False, "path under /tmp")

        state.unlink(missing_ok=True)
        backup = {"pbs_client_runtime": "native", "pbs_client_archives": archives}
        expect(run(root, "pbs-client-backup.yml", backup), True, "first point")
        result = run(root, "pbs-client-backup.yml", backup, case="prune-everything-old")
        expect(result, False, "prune removed the previous point")
        assert "pruning removed the previous point" in result.stdout + result.stderr
        assert "gate-only-secret" not in result.stdout + result.stderr

    print("pbs client: tmpfs, pinned install, identity, preview and verify-before-stop checks passed")


if __name__ == "__main__":
    main()
