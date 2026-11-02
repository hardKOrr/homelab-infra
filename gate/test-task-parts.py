#!/usr/bin/env python3
"""A shared task file split into phase files behaves as the single file it replaced.

Every `ansible/tasks/<dir>/<entry>/<phase>.yml` is imported exactly once, by its entry
`<entry>.yml`, with a bare `import_tasks` (a name and nothing else), and nothing else names
it; parts hold no parts of their own, and every static include in a part resolves from the
part's directory, as Ansible resolves it (`../<file>.yml` for a sibling of the entry).

Against the merge base with master, when this clone holds it and it lacks a part directory
present here (the change that splits a file), every playbook, including each catalog app's
`playbooks/apps/<app>.yml`, resolves to the same tasks (`task_parts.resolve`), and every
split entry inlines to the tasks of its file at the base. Otherwise that comparison prints
a skip (CI's shallow checkout has no merge base). A pull request that splits a file
therefore changes no task.
"""

import os
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_parts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "ansible/tasks"
ENTRIES = sorted(d.with_suffix(".yml") for d in TASKS.rglob("*")
                 if d.is_dir() and d.with_suffix(".yml").is_file() and task_parts.parts(
                     d.with_suffix(".yml")))
assert ENTRIES, "no split task files found"


def static_includes(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        for key in task_parts.NESTED:
            yield from static_includes(task.get(key))
        key, path = task_parts._target(task)
        if key is not None and isinstance(path, str) and "{{" not in path:
            yield path


def targets(node):
    """Every static include or import path anywhere in a parsed YAML file."""
    if isinstance(node, list):
        for item in node:
            yield from targets(item)
    elif isinstance(node, dict):
        key, path = task_parts._target(node)
        if key is not None and isinstance(path, str) and "{{" not in path:
            yield path
        for value in node.values():
            yield from targets(value)


references = {}  # included file -> files that include it
for source in (ROOT / "ansible").rglob("*.yml"):
    for path in targets(yaml.safe_load(source.read_text(encoding="utf-8"))):
        target = Path(os.path.normpath(source.parent / path))
        references.setdefault(target, []).append(source)
for entry in ENTRIES:
    rel = entry.relative_to(ROOT)
    imports = [task_parts.part_of(t, entry) for t in task_parts.load(entry)]
    imported = [p.resolve() for p in imports if p is not None]
    for part in task_parts.parts(entry):
        name = f"{entry.stem}/{part.name}"
        assert imported.count(part.resolve()) == 1, f"{rel}: {name} is not imported exactly once"
        others = [p.relative_to(ROOT) for p in references.get(part, []) if p != entry]
        assert not others, f"{name} is included from outside {rel}: {others}"
        tasks = task_parts.load(part)
        assert not any(task_parts.part_of(t, part) for t in tasks), f"{name} imports a part"
        for path in static_includes(tasks):
            assert (part.parent / path).is_file(), f"{name}: {path} does not resolve from {part.parent}"
    nested = [d for d in entry.with_suffix("").iterdir() if d.is_dir()]
    assert not nested, f"{rel}: parts hold no directories: {nested}"
    print(f"ok   {rel}: {len(task_parts.parts(entry))} part(s), each imported once and in place")



def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=False)


base = next((out.stdout.decode().strip() for ref in ("origin/master", "master")
             if (out := git("merge-base", "HEAD", ref)).returncode == 0), "")
if not base:
    print("skip merge-base comparison: no master merge base in this clone")
    sys.exit(0)
new = [e for e in ENTRIES if git("cat-file", "-e", f"{base}:{e.with_suffix('').relative_to(ROOT)}"
                                 ).returncode != 0]
if not new:
    print(f"skip merge-base comparison: {base[:12]} already has every part directory")
    sys.exit(0)

APPS = list(yaml.safe_load((ROOT / "catalog/applications.yml").read_text())["applications"])
with tempfile.TemporaryDirectory() as tmp:
    archive = git("archive", base, "ansible", "catalog")
    assert archive.returncode == 0, archive.stderr.decode()
    with tarfile.open(fileobj=BytesIO(archive.stdout)) as tar:
        tar.extractall(tmp, filter="data")
    before = Path(tmp)
    playbooks = sorted((ROOT / "ansible/playbooks").rglob("*.yml"))
    for playbook in playbooks:
        rel = playbook.relative_to(ROOT)
        if (before / rel).is_file():
            assert task_parts.resolve(ROOT, playbook) == task_parts.resolve(before, before / rel), \
                f"{rel}: resolved tasks differ from {base[:12]}"
    missing = [app for app in APPS if not (ROOT / f"ansible/playbooks/apps/{app}.yml").is_file()]
    assert not missing, f"catalog apps without a playbook: {missing}"
    print(f"ok   {len(playbooks)} playbooks, all {len(APPS)} catalog apps: same tasks as {base[:12]}")
    for entry in new:
        rel = entry.relative_to(ROOT)
        assert task_parts.inline(entry) == task_parts.load(before / rel), \
            f"{rel}: inlined tasks differ from {base[:12]}"
    print(f"ok   {len(new)} newly split entries inline to their tasks at {base[:12]}")
