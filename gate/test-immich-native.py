#!/usr/bin/env python3
"""Guard Immich's native method: one pinned release, verified downloads, the shared-operation inputs."""

import copy
import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/immich"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/immich.yml").read_text())["immich_defaults"]
app = defaults["app"]

# One release for both methods, and a sha256 for every pinned download.
assert app["image"].endswith(f":v{app['native_version']}")
assert app["machine_learning_image"].split(":")[-1].startswith(f"v{app['native_version']}")
for key in ("source", "node", "uv", "ffmpeg", "countries"):
    assert len(app[f"native_{key}_sha256"]) == 64, key
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])

native = (role / "tasks/native.yml").read_text()
assert "immich-app/immich/archive/refs/tags/v{{ app_config.app.native_version }}.tar.gz" in native
for key in ("source", "node", "uv", "ffmpeg", "countries"):
    assert f"native_{key}_sha256" in native, key

# The Node.js archive's top-level directory is the configured Node root.
assert "node-v{{ app_config.app.native_node_version }}-linux-x64\"" in (role / "defaults/main.yml").read_text()

# The service user needs the unit, the environment file and the models before the first start.
assert native.index("Render the service units") < native.index("Enable and start Immich")
assert native.index("Build the pinned release") < native.index("Render the service units")

# The environment file carries the native paths only on the native method.
env = (role / "templates/immich.env.j2").read_text()
assert "{% if immich_native | bool %}" in env and "IMMICH_MACHINE_LEARNING_URL" in env

# Backup, restore, database dump and Remove run through the shared operations with the
# method's runtime.
assert "pbs_client_runtime: \"{{ app_config.hosting.method" in (role / "tasks/backup.yml").read_text()
assert "database_client_runtime: \"{{ app_config.hosting.method" in (role / "tasks/backup-quiesced.yml").read_text()
assert "recovery_swap_runtime: \"{{ app_config.hosting.method" in (role / "tasks/restore-guest.yml").read_text()
assert "in ['docker', 'native']" in (role / "tasks/restore.yml").read_text()
assert "restart_runtime: \"{{ 'native' if immich_native" in (role / "handlers/main.yml").read_text()
remove_units = yaml.safe_load((role / "tasks/remove-units.yml").read_text())
units = next(t for t in remove_units if "ansible.builtin.set_fact" in t)["ansible.builtin.set_fact"]["_rm_service_names"]
assert units == ["{{ immich_native_server_service }}", "{{ immich_native_ml_service }}"]
# Backup and restore stop the same two names on both methods: the units are named like the services.
assert yaml.safe_load((role / "defaults/main.yml").read_text())["immich_native_server_service"] == "immich-server"
assert yaml.safe_load((role / "defaults/main.yml").read_text())["immich_native_ml_service"] == "immich-machine-learning"

# The ML launcher spawns `python`, so the venv's bin must lead the unit's PATH.
ml_unit = (role / "templates/immich-machine-learning.service.j2").read_text()
assert "Environment=PATH={{ immich_native_root }}/ml/venv/bin:" in ml_unit

# A native VM refuses an iGPU declaration; an LXC binds the shared nodes.
prepare = (role / "tasks/deploy-prepare.yml").read_text()
assert "Refuse a GPU declaration for native Immich on a VM" in prepare
assert "app_config.hosting.method in ['docker', 'native']" in prepare

# Exercise the real remote-copy tasks: applying a file's 0644 mode to the destination
# directory made both the web client and Python package inaccessible to the service user.
tasks = yaml.safe_load(native)
# OpenCV must import on a CPU-only guest, without VAAPI packages supplying libGL.
packages = next(t for t in tasks if "ansible.builtin.apt" in t)["ansible.builtin.apt"]["name"]
for package in ("libgl1", "libglib2.0-0", "libgomp1"):
    assert f"'{package}'" in packages, package
build = next(t for t in tasks if t["name"] == "Immich native | Build the pinned release")
copies = [copy.deepcopy(t) for t in build["block"] if t["name"] in [
    "Immich native | Install the web client", "Immich native | Install the machine-learning package"]]
repair = copy.deepcopy(next(t for t in tasks if t["name"].endswith("package directories traversable")))
for task in copies + [repair]:
    module = next(value for key, value in task.items() if key.startswith("ansible.builtin."))
    module.update(owner=str(os.getuid()), group=str(os.getgid()))
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source, installed = root / "source", root / "installed"
    paths = [("web/build/.well-known/test.json", "build/www/.well-known/test.json"),
             ("machine-learning/immich_ml/__main__.py", "ml/immich_ml/__main__.py")]
    for src, dst in paths:
        path = source / src
        path.parent.mkdir(parents=True)
        path.write_text("canary\n")
        (installed / dst).parent.mkdir(parents=True)
    # The repair must also work on an already-built release, whose build block is skipped.
    for path in [installed / "build/www", installed / "ml/immich_ml"]:
        path.chmod(0o644)
    playbook = root / "copy.yml"
    play = {"hosts": "localhost", "gather_facts": False,
            "vars": {"immich_native_build_dir": str(source), "immich_native_root": str(installed)},
            "tasks": [repair] + copies + [repair]}
    playbook.write_text(yaml.safe_dump([play]))
    ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
    for repeat in [False, True]:
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        for _, dst in paths:
            path = installed / dst
            assert path.read_text() == "canary\n"
            assert path.parent.stat().st_mode & 0o111 == 0o111
            assert path.parent.parent.stat().st_mode & 0o111 == 0o111
        if repeat:
            assert re.search(r"changed=0\b", result.stdout), result.stdout
    # Remove includes the hook directly, outside the role, so defaults are not implicit.
    play["tasks"] = [
        {"ansible.builtin.include_tasks": str(role / "tasks/remove-units.yml")},
        {"ansible.builtin.assert": {"that": [
            "_rm_service_names == ['immich-server', 'immich-machine-learning']"]}},
    ]
    playbook.write_text(yaml.safe_dump([play]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr

print("Immich native pins one release with verified downloads and reuses the shared backup, restore and remove")
