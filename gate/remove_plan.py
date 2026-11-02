#!/usr/bin/env python3
"""Resolve Remove App, or Restore App, into the task sequence one app's run takes.

`plan()` resolves Remove's Play 2 (the stop on the app's host); `plan_all()` resolves every
play of Remove, or of another playbook with role hooks (`playbook=RESTORE`), each play
starting with a step that holds its `vars`. Blocks are flattened with their
conditions, the role hooks (`{{ _rm_hooks }}/<file>`, `{{ _ra_hooks }}/<file>`), the role
task files a hook includes and `tasks/proxmox/detach-app-gpu.yml` are inlined, and conditions on the app's identity are
decided for the given app and instance: an app-name comparison or list, a hook file's
presence, a play variable that is one of those, and a flag a hook declares
(`_rm_database_backend`, `_rm_keeps_guest_record`, true only when a hook sets it).
Inside other strings the same comparisons and flags are replaced by `True`/`False`.
Every other condition stays as text. Each step is
(name, module, arguments, conditions, and `via`, the inlined files it came
through); an included task file that is not inlined is
represented by its parsed content, so a renamed file with the same tasks compares equal.
A hook task that only declares flags is not a step. A phase file the playbook imports
(PHASES) is inlined without entering `via`; `load_playbook()` returns a playbook with its
phases expanded, as one file, for suites that read the playbook's tasks.

Usage: remove_plan.py <repo> <app> [<instance>] [--all]  prints the plan as JSON lines.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml

INLINE = ("tasks/proxmox/detach-app-gpu.yml",)
# A playbook's phase files: imported statically and inlined without entering `via`, so a
# phase split leaves every step, and the plan, exactly as it was.
PHASES = ("ansible/tasks/removal/", "ansible/tasks/recovery/restore-app-",
          "ansible/tasks/recovery/restore-guest-")
CONTROL = {"name", "when", "vars", "loop", "register", "block", "rescue", "always",
           "no_log", "changed_when", "failed_when", "args", "become", "delegate_to",
           "tags", "until", "retries", "delay", "notify", "environment", "loop_control"}


def listify(when) -> list[str]:
    if when is None:
        return []
    return [str(w).strip() for w in (when if isinstance(when, list) else [when])]


FLAGS = ("_rm_database_backend", "_rm_keeps_guest_record")
REMOVE = "ansible/playbooks/apps/remove.yml"
RESTORE = "ansible/playbooks/maintenance/restore-app.yml"
GUEST = "ansible/playbooks/maintenance/restore-guest.yml"
HOOK = re.compile(r"\{\{ _r[am]_hooks \}\}")


def subjects(app: str, instance: str) -> dict:
    return {
        r"hostvars\['localhost'\]\._rm_app_name": app,
        r"\(remove_app \| default\(instance\)\)": instance,
        r"\(app \| default\(instance\)\)": app,
        r"hostvars\['localhost'\]\._ra_app": app,
        r"(?<![\w.])_ra_app(?!\w)": app,
    }


def substitute(text: str, app: str, instance: str, hooks: Path, flags: set) -> str:
    """Replace app-identity comparisons, hook presence and flags inside a string."""
    for pattern, value in subjects(app, instance).items():
        text = re.sub(rf"{pattern} == '([\w-]+)'", lambda m: str(value == m.group(1)), text)
        text = re.sub(rf"{pattern} in \[([^\]]*)\]",
                      lambda m: str(value in re.findall(r"'([\w-]+)'", m.group(1))), text)
    text = re.sub(r"\(_r[am]_hooks ~ '/([\w.-]+)'\) is file",
                  lambda m: str((hooks / m.group(1)).is_file()), text)
    for flag in FLAGS:
        text = re.sub(rf"\(?{flag} \| default\(false\) \| bool\)?", str(flag in flags), text)
    return text


def decide(condition: str, app: str, instance: str, hooks: Path,
           flags: set = frozenset(), play_vars: dict | None = None) -> bool | None:
    """True/False for an app-identity condition, None for any other condition."""
    for name, value in (play_vars or {}).items():  # a play variable decided like a literal
        if value in ("{{ True }}", "{{ False }}"):
            condition = re.sub(rf"\b{name} \| bool\b", value[3:-3], condition)
    text = substitute(condition, app, instance, hooks, flags)
    if text in ("True", "False", "not (True)", "not (False)", "not True", "not False"):
        return text in ("True", "not (False)", "not False")
    return None


def include_target(task: dict) -> str | None:
    for key in ("ansible.builtin.include_tasks", "include_tasks", "ansible.builtin.import_tasks"):
        if key in task:
            return str(task[key])
    return None


def resolve(path: str, base: Path, repo: Path, hooks: Path) -> Path:
    if HOOK.search(path):
        return hooks / path.split("}}/", 1)[1]
    return (base / path).resolve()


def walk(tasks, base: Path, repo: Path, app: str, instance: str, inherited: list[str],
         via: tuple = (), flags: set | None = None, play_vars: dict | None = None,
         hooks: Path | None = None):
    hooks = hooks or repo / "ansible/roles" / app / "tasks"
    flags = set() if flags is None else flags
    for task in tasks or []:
        conditions = []
        skip = False
        for condition in inherited + listify(task.get("when")):
            verdict = decide(condition, app, instance, hooks, flags, play_vars)
            if verdict is False:
                skip = True
            elif verdict is None:
                conditions.append(condition)
        if skip:
            continue
        if "block" in task:
            yield from walk(task["block"], base, repo, app, instance, conditions, via, flags,
                            play_vars, hooks)
            continue
        facts = task.get("ansible.builtin.set_fact") or {}
        if via and facts and set(facts) <= set(FLAGS):  # a hook declaring flags
            flags.update(name for name, value in facts.items() if value is True)
            continue
        target = include_target(task)
        if target is not None and "{{" in target and not HOOK.search(target):
            module, arguments = "include_tasks", templated(target, base, repo)
        elif target is not None:
            file = resolve(target, base, repo, hooks)
            relative = file.relative_to(repo.resolve()).as_posix() if file.is_relative_to(
                repo.resolve()) else str(file)
            if relative.startswith(PHASES) and "ansible.builtin.import_tasks" in task:
                yield from walk(yaml.safe_load(file.read_text()) or [], file.parent, repo, app,
                                instance, conditions, via, flags, play_vars, hooks)
                continue
            if HOOK.search(target) or relative.endswith(INLINE) or (
                    via and relative.startswith("ansible/roles/")):
                inner = yaml.safe_load(file.read_text()) or []
                if task.get("vars"):  # vars on an inlining include reach every inner task
                    inner = [{**t, "vars": {**task["vars"], **t.get("vars", {})}} for t in inner]
                yield from walk(inner, file.parent, repo, app, instance, conditions,
                                via + (relative,), flags, play_vars, hooks)
                continue
            module = "include_tasks"
            arguments = yaml.safe_load(file.read_text())  # content, not path
            if relative.startswith("ansible/tasks/"):
                arguments = relative  # shared seam: same file on both sides
        else:
            module = next(k for k in task if k not in CONTROL)
            arguments = task[module]
        extra = {k: task[k] for k in sorted(task) if k in CONTROL - {"name", "when", "block"}}
        arguments, extra = (rewrite(x, lambda t: substitute(t, app, instance, hooks, flags))
                            for x in (arguments, extra))
        yield {"name": task.get("name"), "module": module, "arguments": arguments,
               "extra": extra, "when": conditions, "via": list(via)}


def templated(target: str, base: Path, repo: Path) -> str:
    """A templated path as text, its literal directory prefix made repo-relative."""
    prefix, rest = target.split("{{", 1)
    if not prefix:
        return target
    folder = os.path.normpath(base / prefix)
    root = os.path.normpath(repo.resolve())
    if folder.startswith(root + os.sep):
        folder = folder[len(root) + 1:]
    return folder + ("/" if prefix.endswith("/") else "") + "{{" + rest


def rewrite(value, change):
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        return [rewrite(v, change) for v in value]
    if isinstance(value, dict):
        return {k: rewrite(v, change) for k, v in value.items()}
    return value


def plan_all(repo: Path, app: str, instance: str | None = None,
             only: str | None = None, playbook: str = REMOVE,
             role: str | None = None) -> list[dict]:
    """`role` is the role whose hooks run, when it is not the app's own."""
    playbook = repo / playbook
    hooks = repo / "ansible/roles" / (role or app) / "tasks"
    instance = instance or app
    flags: set = set()  # Play 1's hooks declare them; later plays read them
    steps = []
    for play in yaml.safe_load(playbook.read_text()):
        name = str(play.get("name", ""))
        play_vars = {}
        for key, value in (play.get("vars") or {}).items():
            play_vars[key] = rewrite(value, lambda t: substitute(t, app, instance, hooks, flags))
        found = []
        for section in ("pre_tasks", "tasks"):
            found += walk(play.get(section), playbook.parent, repo, app, instance, [], (),
                          flags, play_vars, hooks)
        play_vars = {k: rewrite(v, lambda t: substitute(t, app, instance, hooks, flags))
                     for k, v in (play.get("vars") or {}).items()}  # flags now known
        if only is None:
            steps.append({"play": name, "vars": play_vars})
        if only is None or name.startswith(only):
            steps += found
    return steps


def phase_file(task: dict, base: Path) -> Path | None:
    """The phase file a task statically imports, if it is one."""
    target = task.get("ansible.builtin.import_tasks")
    if not isinstance(target, str) or "{{" in target:
        return None
    file = (base / target).resolve()  # also inside a copy of the tree
    return file if any("/" + phase in file.as_posix() for phase in PHASES) else None


def rebase(tasks: list, source: Path, base: Path) -> list:
    """Tasks read from directory `source`, their include paths made relative to `base`."""
    out = []
    for task in tasks or []:
        task = dict(task)
        for key in ("block", "rescue", "always"):
            if key in task:
                task[key] = rebase(task[key], source, base)
        for key in ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks"):
            value = task.get(key)
            if isinstance(value, str) and not value.startswith("{{"):
                prefix, brace, rest = value.partition("{{")
                moved = os.path.relpath(os.path.normpath(source / prefix), base)
                task[key] = moved + ("/" if prefix.endswith("/") else "") + brace + rest
        out.append(task)
    return out


def expand(tasks: list, base: Path) -> list:
    out = []
    for task in tasks or []:
        file = phase_file(task, base)
        if file is None:
            out.append(task)
        else:
            out += rebase(expand(yaml.safe_load(file.read_text()) or [], file.parent),
                          file.parent, base)
    return out


def load_playbook(path: Path) -> list:
    """A playbook's plays as one file: each phase import is replaced by the phase file's
    tasks, their include paths rebased to the playbook directory."""
    plays = yaml.safe_load(Path(path).read_text())
    for play in plays:
        for section in ("pre_tasks", "tasks", "post_tasks"):
            if section in play:
                play[section] = expand(play[section], Path(path).parent)
    return plays


def playbook_text(path: Path) -> str:
    """A playbook's text followed by the text of every phase file it imports."""
    path = Path(path)
    files = [phase_file(task, path.parent) for play in yaml.safe_load(path.read_text())
             for section in ("pre_tasks", "tasks", "post_tasks") for task in play.get(section) or []]
    return "\n".join([path.read_text()] + [f.read_text() for f in files if f is not None])


def plan(repo: Path, app: str, instance: str | None = None) -> list[dict]:
    return plan_all(repo, app, instance, only="Remove App | Stop")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--all"]
    resolve_plan = plan_all if "--all" in sys.argv else plan
    root, name = Path(args[0]), args[1]
    for step in resolve_plan(root, name, args[2] if len(args) > 2 else None):
        print(json.dumps(step, sort_keys=True, default=str))
