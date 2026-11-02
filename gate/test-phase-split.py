#!/usr/bin/env python3
"""Remove App, Restore App and Restore Guest orchestrate phase files that change nothing.

Each phase file (remove_plan.PHASES) is imported once, statically and unconditionally, by
one of the three playbooks, and every include path in a playbook expanded by
remove_plan.load_playbook names an existing file. Against the merge base with master,
when this clone holds it and it predates the phase files, the expanded playbooks equal
the base playbooks and the resolved plan of every catalog app is unchanged; otherwise
that comparison prints a skip (CI's shallow checkout has no merge base)."""

import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remove_plan import GUEST, PHASES, REMOVE, RESTORE, load_playbook, phase_file, plan_all  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOKS = (REMOVE, RESTORE, GUEST)
APPS = list(yaml.safe_load((ROOT / "catalog/applications.yml").read_text())["applications"])
ALIASES = yaml.safe_load((ROOT / "gate/structure.yml").read_text())["role_aliases"]
CONTROL = {"name", "ansible.builtin.import_tasks"}


def includes(tasks):
    for task in tasks or []:
        for key in ("block", "rescue", "always"):
            yield from includes(task.get(key))
        for key in ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks"):
            if isinstance(task.get(key), str):
                yield task[key]


# Every phase file belongs to exactly one import in one of the three playbooks.
imported = []
for playbook in PLAYBOOKS:
    path = ROOT / playbook
    for play in yaml.safe_load(path.read_text()):
        for section in ("pre_tasks", "tasks", "post_tasks"):
            for task in play.get(section) or []:
                file = phase_file(task, path.parent)
                if file is not None:
                    assert set(task) <= CONTROL, f"{playbook}: {task['name']} carries {set(task) - CONTROL}"
                    imported.append(file.relative_to(ROOT).as_posix())
phases = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "ansible/tasks").rglob("*.yml")
                if p.relative_to(ROOT).as_posix().startswith(PHASES))
assert sorted(imported) == phases, f"imported {sorted(imported)}, present {phases}"
for playbook in PLAYBOOKS:
    path = ROOT / playbook
    for play in load_playbook(path):
        for section in ("pre_tasks", "tasks", "post_tasks"):
            for target in includes(play.get(section)):
                prefix = target.split("{{")[0]
                if "{{" not in target:
                    assert (path.parent / target).is_file(), f"{playbook}: {target}"
                elif prefix:  # a templated name in a literal directory
                    assert Path(f"{path.parent / prefix}x").parent.is_dir(), f"{playbook}: {target}"
print(f"ok   {len(phases)} phase files, each imported once without conditions")


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=False)


base = next((out.stdout.decode().strip() for ref in ("origin/master", "master")
             if (out := git("merge-base", "HEAD", ref)).returncode == 0), "")
if not base:
    print("skip merge-base comparison: no master merge base in this clone")
    sys.exit(0)
if git("cat-file", "-e", f"{base}:ansible/tasks/removal").returncode == 0:
    print(f"skip merge-base comparison: {base[:12]} already has the phase files")
    sys.exit(0)

with tempfile.TemporaryDirectory() as tmp:
    archive = git("archive", base, "ansible", "catalog", "gate/structure.yml")
    assert archive.returncode == 0, archive.stderr.decode()
    with tarfile.open(fileobj=BytesIO(archive.stdout)) as tar:
        tar.extractall(tmp, filter="data")
    before = Path(tmp)
    for playbook in PLAYBOOKS:
        assert load_playbook(ROOT / playbook) == yaml.safe_load((before / playbook).read_text()), \
            f"{playbook} differs from {base[:12]} once its phases are expanded"
        changed = []
        for app in APPS:
            role = ALIASES.get(app) or app if playbook == RESTORE else None
            if plan_all(before, app, playbook=playbook, role=role) != plan_all(
                    ROOT, app, playbook=playbook, role=role):
                changed.append(app)
        assert not changed, f"{playbook}: the plan changed for {changed}"
        print(f"ok   {playbook}: same as {base[:12]} for all {len(APPS)} apps")
