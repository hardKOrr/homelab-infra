#!/usr/bin/env python3
"""Read a shared task file whose phases live in part files, and resolve a playbook's tasks.

An entry file `ansible/tasks/<dir>/<entry>.yml` may move a run of its tasks verbatim to
`<entry>/<phase>.yml` and import it with a bare `import_tasks` (a `name` and nothing else),
which Ansible treats exactly as if the tasks were written in place. A part refers to the
entry's sibling task files as `../<file>.yml`, because Ansible resolves an include against
the directory of the file that holds it.

`parts(entry)` lists an entry's part files. `inline(entry)` returns the entry's tasks with
every part spliced in at its import and the part's include paths rebased to the entry's
directory (`remove_plan.rebase`), so a test reads the file as one list. `text(entry)` is the entry followed by its
parts, for tests that search the source.

`resolve(repo, playbook)` flattens a playbook into the leaf tasks it can run: plays,
`import_playbook`, `roles:`, `include_role`/`import_role` and every `include_tasks`/
`import_tasks` whose path is static or uses `playbook_dir`/`role_path`. Each leaf carries the
keywords of every block, role and include above it, minus names and file paths, so a file
that only moves tasks between files leaves the result unchanged.

Usage: task_parts.py <repo> <playbook>  prints the resolved tasks as JSON lines.
"""

from __future__ import annotations

import copy
import json
import sys
from functools import lru_cache
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remove_plan import rebase  # noqa: E402

IMPORT = "ansible.builtin.import_tasks"
INCLUDES = ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks",
            "include_tasks", "import_tasks")
ROLES = ("ansible.builtin.include_role", "ansible.builtin.import_role",
         "include_role", "import_role")
SECTIONS = ("pre_tasks", "roles", "tasks", "post_tasks", "handlers")
NESTED = ("block", "rescue", "always")


@lru_cache(maxsize=None)
def _cached(path: Path) -> list:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def load(path: Path) -> list:
    """A file's parsed tasks; callers never modify them."""
    return _cached(Path(path).resolve())


def parts(entry: Path) -> list[Path]:
    return sorted(entry.with_suffix("").glob("*.yml"))


def part_of(task, entry: Path) -> Path | None:
    """The part file a bare import in `entry` names, or None."""
    if not isinstance(task, dict) or not set(task) <= {"name", IMPORT}:
        return None
    target = task.get(IMPORT)
    if isinstance(target, str) and target.startswith(entry.stem + "/"):
        return entry.parent / target
    return None


def _splice(tasks, entry: Path) -> list:
    out = []
    for task in tasks:
        part = part_of(task, entry)
        if part is not None:
            out += rebase(_splice(load(part), entry), part.parent, entry.parent)
            continue
        if isinstance(task, dict):
            task = {k: (_splice(v, entry) if k in NESTED else v) for k, v in task.items()}
        out.append(task)
    return out


def inline(entry: Path) -> list:
    """Also loads any other YAML file, unchanged."""
    data = load(entry)
    return copy.deepcopy(_splice(data, Path(entry)) if isinstance(data, list) else data)


def text(entry: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in [entry, *parts(entry)])


def _target(task) -> tuple[str | None, str | None]:
    for key in INCLUDES:
        if key in task:
            value = task[key]
            return key, value.get("file") if isinstance(value, dict) else value
    return None, None


def _find(path: str, here: Path, chain: list[Path], playbook: Path, role: Path | None):
    """The file Ansible would load: the including file's directory, then each file above it."""
    path = path.replace("{{ playbook_dir }}", str(playbook.parent))
    if role is not None:
        path = path.replace("{{ role_path }}", str(role))
    if "{{" in path:
        return None
    candidates = [here.parent / path] + [c.parent / path for c in reversed(chain)]
    if role is not None:
        candidates.append(role / "tasks" / path)
    candidates.append(playbook.parent / path)
    return next((c.resolve() for c in candidates if c.is_file()), None)


def _keywords(task: dict, drop: tuple) -> dict:
    return {k: v for k, v in sorted(task.items()) if k != "name" and k not in drop}


class Resolver:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self.roles = self.repo / "ansible/roles"

    def role(self, name: str, tasks_from: str, inherited, chain, playbook, depth):
        file = self.roles / name / "tasks" / tasks_from
        if file.suffix not in (".yml", ".yaml"):
            file = file.with_suffix(".yml")
        return self.tasks(load(file), file, inherited, chain + [file], playbook,
                          self.roles / name, depth)

    def tasks(self, tasks, here: Path, inherited, chain, playbook, role, depth):
        assert depth < 40, f"include depth exceeded at {here}"
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            if "block" in task:
                scope = inherited + [_keywords(task, NESTED)]
                for key in NESTED:
                    marker = scope + ([{"section": key}] if key != "block" else [])
                    yield from self.tasks(task.get(key), here, marker, chain, playbook, role,
                                          depth + 1)
                continue
            key, path = _target(task)
            if key is not None:
                file = _find(str(path), here, chain, playbook, role)
                if file is not None:
                    value = task[key]
                    extra = {k: v for k, v in value.items() if k != "file"} if isinstance(
                        value, dict) else {}
                    scope = inherited + [_keywords(task, INCLUDES) | extra]
                    scope = [s for s in scope if s]
                    yield from self.tasks(load(file), file, scope, chain + [file], playbook,
                                          role, depth + 1)
                    continue
            role_key = next((k for k in ROLES if k in task), None)
            if role_key is not None:
                spec = task[role_key]
                name = str(spec.get("name", ""))
                if "{{" not in name and (self.roles / name).is_dir():
                    extra = {k: v for k, v in spec.items() if k not in ("name", "tasks_from")}
                    scope = [s for s in inherited + [_keywords(task, ROLES) | extra] if s]
                    yield from self.role(name, str(spec.get("tasks_from", "main")), scope,
                                         chain, playbook, depth + 1)
                    continue
            yield {"name": task.get("name"), "task": _keywords(task, ()), "scope": inherited}

    def playbook(self, playbook: Path, depth: int = 0):
        playbook = playbook.resolve()
        for play in load(playbook):
            if "import_playbook" in play or "ansible.builtin.import_playbook" in play:
                target = play.get("import_playbook") or play["ansible.builtin.import_playbook"]
                if "{{" not in str(target):
                    yield from self.playbook(playbook.parent / target, depth + 1)
                    continue
            keywords = _keywords(play, SECTIONS)
            yield {"play": play.get("name"), "keywords": keywords}
            for section in SECTIONS:
                if section == "roles":
                    for entry in play.get("roles") or []:
                        spec = {"role": entry} if isinstance(entry, str) else dict(entry)
                        name = str(spec.pop("role", spec.pop("name", "")))
                        yield {"section": "roles", "role": name}
                        if (self.roles / name).is_dir():
                            scope = [spec] if spec else []
                            yield from self.role(name, "main", scope, [], playbook, depth + 1)
                    continue
                if play.get(section):
                    yield {"section": section}
                    yield from self.tasks(play[section], playbook, [], [playbook], playbook,
                                          None, depth + 1)


def resolve(repo: Path, playbook: Path) -> list[dict]:
    return list(Resolver(repo).playbook(playbook))


if __name__ == "__main__":
    for step in resolve(Path(sys.argv[1]), Path(sys.argv[2])):
        print(json.dumps(step, sort_keys=True, default=str))
