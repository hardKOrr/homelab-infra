#!/usr/bin/env python3
"""Resolve Remove App's Play 2 into the task sequence one app's removal runs.

Blocks are flattened with their conditions, the role hooks (`{{ _rm_hooks }}/<file>`) and
`tasks/proxmox/detach-app-gpu.yml` are inlined, and conditions on the app's identity are
decided for the given app and instance. Every other condition stays as text. Each step is
(name, module, arguments, conditions, and `via`, the inlined files it came
through); an included task file that is not inlined is
represented by its parsed content, so a renamed file with the same tasks compares equal.

Usage: remove_plan.py <repo> <app> [<instance>]  prints the plan as JSON lines.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

INLINE = ("tasks/proxmox/detach-app-gpu.yml",)
CONTROL = {"name", "when", "vars", "loop", "register", "block", "rescue", "always",
           "no_log", "changed_when", "failed_when", "args", "become", "delegate_to",
           "tags", "until", "retries", "delay", "notify", "environment", "loop_control"}


def listify(when) -> list[str]:
    if when is None:
        return []
    return [str(w).strip() for w in (when if isinstance(when, list) else [when])]


def decide(condition: str, app: str, instance: str, hooks: Path) -> bool | None:
    """True/False for an app-identity condition, None for any other condition."""
    subjects = {
        r"hostvars\['localhost'\]\._rm_app_name": app,
        r"\(remove_app \| default\(instance\)\)": instance,
        r"\(app \| default\(instance\)\)": app,
    }
    for pattern, value in subjects.items():
        match = re.fullmatch(rf"{pattern} == '([\w-]+)'", condition)
        if match:
            return value == match.group(1)
    match = re.fullmatch(r"\(_rm_hooks ~ '/([\w.-]+)'\) is file", condition)
    if match:
        return (hooks / match.group(1)).is_file()
    return None


def include_target(task: dict) -> str | None:
    for key in ("ansible.builtin.include_tasks", "include_tasks", "ansible.builtin.import_tasks"):
        if key in task:
            return str(task[key])
    return None


def resolve(path: str, base: Path, repo: Path, hooks: Path) -> Path:
    if "{{ _rm_hooks }}" in path:
        return hooks / path.split("}}/", 1)[1]
    return (base / path).resolve()


def walk(tasks, base: Path, repo: Path, app: str, instance: str, inherited: list[str],
         via: tuple = ()):
    hooks = repo / "ansible/roles" / app / "tasks"
    for task in tasks or []:
        conditions = []
        skip = False
        for condition in inherited + listify(task.get("when")):
            verdict = decide(condition, app, instance, hooks)
            if verdict is False:
                skip = True
            elif verdict is None:
                conditions.append(condition)
        if skip:
            continue
        if "block" in task:
            yield from walk(task["block"], base, repo, app, instance, conditions, via)
            continue
        target = include_target(task)
        if target is not None:
            file = resolve(target, base, repo, hooks)
            relative = file.relative_to(repo.resolve()).as_posix() if file.is_relative_to(
                repo.resolve()) else str(file)
            if "{{ _rm_hooks }}" in target or relative.endswith(INLINE):
                inner = yaml.safe_load(file.read_text()) or []
                if task.get("vars"):  # vars on an inlining include reach every inner task
                    inner = [{**t, "vars": {**task["vars"], **t.get("vars", {})}} for t in inner]
                yield from walk(inner, file.parent, repo, app, instance, conditions,
                                via + (relative,))
                continue
            module = "include_tasks"
            arguments = yaml.safe_load(file.read_text())  # content, not path
            if relative.startswith("ansible/tasks/"):
                arguments = relative  # shared seam: same file on both sides
        else:
            module = next(k for k in task if k not in CONTROL)
            arguments = task[module]
        extra = {k: task[k] for k in sorted(task) if k in CONTROL - {"name", "when", "block"}}
        yield {"name": task.get("name"), "module": module, "arguments": arguments,
               "extra": extra, "when": conditions, "via": list(via)}


def plan(repo: Path, app: str, instance: str | None = None) -> list[dict]:
    playbook = repo / "ansible/playbooks/apps/remove.yml"
    play = next(p for p in yaml.safe_load(playbook.read_text())
                if str(p.get("name", "")).startswith("Remove App | Stop"))
    steps = []
    for section in ("pre_tasks", "tasks"):
        steps += walk(play.get(section), playbook.parent, repo, app, instance or app, [])
    return steps


if __name__ == "__main__":
    root, name = Path(sys.argv[1]), sys.argv[2]
    for step in plan(root, name, sys.argv[3] if len(sys.argv) > 3 else None):
        print(json.dumps(step, sort_keys=True, default=str))
