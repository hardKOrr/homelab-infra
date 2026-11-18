#!/usr/bin/env python3
"""Check, from one table, which shared task files each app's Deploy, Backup and Restore reach.

For every catalog app that deploys through `playbooks/apps/<app>.yml` (not the full
playbooks in FULL), and for each hosting method its defaults support (`docker`,
`kubernetes`, `native`), the suite resolves the run with `task_parts.resolve`, decides every
condition on `app_config.hosting.method` (and every play variable or fact computed only
from it) for that method, and lists the files under `ansible/tasks/` that the app's own
playbook and role code include, in first-reached order; what a shared file includes in
turn belongs to that file. Deploy starts at the app's playbook; Backup and Restore at its
role's `backup.yml` and `restore.yml` hooks. An include whose path is still templated is
listed as written. `gate/app-matrix.yml` records, per app, the default method, the
supported combinations and these lists; the suite fails on any difference, so a change to
what an app's run reaches shows up as a table diff. The lists depend on the resolved run,
not on how a playbook is laid out, so moving an app onto a shared Deploy leaves its row
unchanged unless the run itself changes.

On top of the table, RULES must hold for every row, checked on every shared file in the
trace, nested ones included: every Deploy loads its config through the shared loader, a
Kubernetes Deploy applies a manifest and creates no guest, and so on.

The rules hold for `playbooks/apps/deploy.yml` as well, traced for every app as if that
app's shim imported it. `--preview [app ...]` prints how each app's Deploy row would change
on that move.

`python gate/test-app-matrix.py --update` prints the table computed from the tree.
"""

from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gate"))
import task_parts  # noqa: E402

TABLE = ROOT / "gate/app-matrix.yml"
FULL = {"k3s-cluster", "opnsense", "pbs", "proxmox-datacenter-manager"}
# Apps in the table that keep their own Deploy playbook instead of a deploy.yml shim:
# Caddy's Seed path and edge copy.
OWN_DEPLOY = {"caddy"}
ALIASES = yaml.safe_load((ROOT / "gate/structure.yml").read_text())["role_aliases"]
# The hosting method of the app or of a Restore's target, however it is reached.
METHOD = re.compile(r"[\w\[\]'\".]*config\.hosting\.method")
INCLUDE_KEYS = task_parts.INCLUDES + task_parts.ROLES
ENV = jinja2.Environment(undefined=jinja2.StrictUndefined)
ENV.filters["bool"] = lambda value: value if isinstance(value, bool) else str(value).lower() in (
    "true", "yes", "1", "on")
ENV.tests["file"] = lambda path: Path(path).is_file()
SHARED_DEPLOY = ROOT / "ansible/playbooks/apps/deploy.yml"


def apps() -> list[str]:
    found = []
    for path in sorted((ROOT / "ansible/vars/app-defaults").glob("*.yml")):
        app = path.stem
        if app.startswith("_") or app in FULL:
            continue
        if (ROOT / f"ansible/playbooks/apps/{app}.yml").is_file():
            found.append(app)
    return found


def defaults(app: str) -> dict:
    data = yaml.safe_load((ROOT / f"ansible/vars/app-defaults/{app}.yml").read_text())
    return data[f"{app.replace('-', '_')}_defaults"]


def role_of(app: str) -> str | None:
    return ALIASES.get(app, app)


@lru_cache(maxsize=None)
def compiled(text: str):
    return ENV.compile_expression(text, undefined_to_none=False)


def role_vars(steps: list[dict]) -> dict:
    """The defaults and vars of every role a run passes through."""
    found = {}
    roles = {f.split("/")[2] for step in steps for f in step.get("via") or []
             if f.startswith("ansible/roles/")}
    for role in sorted(roles):
        for name in ("defaults", "vars"):
            path = ROOT / f"ansible/roles/{role}/{name}/main.yml"
            if path.is_file():
                found.update(yaml.safe_load(path.read_text()) or {})
    return found


class Decider:
    """Decides conditions for one hosting method; None when a condition is not decided.

    A play, include or role variable, or a fact, whose value is an expression decided from
    the method alone, is decided too, so `when: not (_app_k8s | bool)` is decided.
    """

    def __init__(self, method: str, steps: list[dict], seed: dict | None = None):
        self.method = method
        self.known: dict = dict(seed or {})
        self.cache: dict = {}
        self.names = None
        candidates = dict(role_vars(steps))
        for step in steps:
            for scope in [step.get("keywords") or {}, *(step.get("scope") or []),
                          step.get("task") or {}]:
                for name, value in (scope.get("vars") or {}).items():
                    candidates.setdefault(name, value)
            fact = (step.get("task") or {}).get("ansible.builtin.set_fact") or {}
            for name, value in fact.items() if isinstance(fact, dict) else []:
                candidates.setdefault(name, value)
        expressions = {name: value.strip()[2:-2] for name, value in candidates.items()
                       if isinstance(value, str) and value.strip().startswith("{{")
                       and value.strip().endswith("}}") and value.count("{{") == 1}
        for _ in range(3):
            for name, expression in expressions.items():
                if name not in self.known:
                    result = self.evaluate(expression)
                    if result is not None:
                        self.known[name] = result
                        self.names = None
                        self.cache.clear()

    def value(self, expression):
        """The expression's value for this method, or None when it is not decided."""
        expression = str(expression)
        if expression in self.cache:
            return self.cache[expression]
        if self.names is None:
            self.names = re.compile(r"\b(%s)\b" % "|".join(map(re.escape, self.known))
                                    if self.known else r"(?!)")
        value = None
        if METHOD.search(expression) or self.names.search(expression):
            try:
                value = compiled(METHOD.sub(repr(self.method), expression).strip())(**self.known)
            except Exception:  # noqa: BLE001 - anything not decidable stays undecided
                value = None
        self.cache[expression] = value if isinstance(value, (bool, str)) else None
        return self.cache[expression]

    def evaluate(self, expression) -> bool | None:
        value = self.value(expression)
        return value if isinstance(value, bool) else None

    def render(self, text: str) -> str | None:
        """A templated include path or role name with every expression decided, else None."""
        parts = re.split(r"\{\{(.*?)\}\}", text, flags=re.S)
        for index in range(1, len(parts), 2):
            value = self.value(parts[index])
            if not isinstance(value, str):
                return None
            parts[index] = value
        return "".join(parts)

    def runs(self, step: dict) -> bool:
        for scope in [*(step.get("scope") or []), step.get("task") or {}]:
            when = scope.get("when")
            for condition in (when if isinstance(when, list) else [when]):
                if condition is None:
                    continue
                if isinstance(condition, bool):
                    if not condition:
                        return False
                    continue
                if self.evaluate(condition) is False:
                    return False
        return True


def guest_play(keywords: dict) -> bool:
    """A play on the instance's deploy group, which only a guest Deploy fills."""
    return str(keywords.get("hosts", "")).startswith("deploy_")


def adds_guest(step: dict) -> bool:
    """An add_host into the deploy group, its group named directly or by an include var."""
    task = step.get("task") or {}
    add = task.get("ansible.builtin.add_host") or task.get("add_host")
    if not isinstance(add, dict):
        return False
    groups = str(add.get("groups", ""))
    named = re.fullmatch(r"\{\{\s*(\w+).*\}\}", groups.strip())
    if named:
        for scope in reversed(step.get("scope") or []):
            if named.group(1) in (scope.get("vars") or {}):
                groups = str(scope["vars"][named.group(1)])
                break
    return groups.startswith("deploy_")


def ends_play(step: dict, decider: "Decider") -> bool:
    """A `meta: end_play` that runs for certain for this method."""
    task = step.get("task") or {}
    if (task.get("ansible.builtin.meta") or task.get("meta")) != "end_play":
        return False
    for scope in [*(step.get("scope") or []), task]:
        when = scope.get("when")
        for condition in (when if isinstance(when, list) else [when]):
            if condition is not None and condition is not True and decider.evaluate(
                    condition) is not True:
                return False
    return True


def include_target(task: dict):
    for key in INCLUDE_KEYS:
        target = task.get(key)
        if isinstance(target, dict):
            target = target.get("file") or target.get("name")
        if isinstance(target, str):
            return target
    return None


def templated(steps: list[dict]) -> list[str]:
    return [t for t in (include_target(step.get("task") or {}) for step in steps)
            if t and "{{" in t]


def reached(run, method: str, deep: bool = False, seed: dict | None = None) -> list[str]:
    """The shared task files a run reaches for one method, in first-reached order.

    The table lists the shared files the app's own code includes; what a shared file
    includes in turn belongs to that file. `deep` lists every shared file in the trace,
    for the rules, so a guest created inside a shared file still breaks a Kubernetes rule.

    `run(render)` resolves the run; it is resolved again with templated includes decided
    for the method when the first pass meets one. A play on the deploy group counts only
    after a reached task adds a guest to it.
    """
    steps = run(None)
    decider = Decider(method, steps, seed)
    if any(decider.render(target) for target in templated(steps)):
        steps = run(decider.render)
    files: list[str] = []
    guest = False
    skip = False

    def add(name: str):
        if name not in files:
            files.append(name)

    for step in steps:
        if "play" in step:
            skip = guest_play(step.get("keywords") or {}) and not guest
            continue
        if skip or "via" not in step or not decider.runs(step):
            continue
        guest = guest or adds_guest(step)
        if ends_play(step, decider):
            skip = True
            continue
        shared = [f for f in step["via"] if f.startswith("ansible/tasks/")]
        for name in shared if deep else shared[:1]:
            add(name.removeprefix("ansible/"))
        if shared:
            continue
        target = include_target(step["task"])
        if target and "{{" in target:
            add(re.sub(r"^(\.\./)+", "", target))
    return files


@lru_cache(maxsize=None)
def deploy_steps(app: str, render=None, shared: bool = False) -> list[dict]:
    resolver = task_parts.Resolver(ROOT, trace=True, render=render)
    path = SHARED_DEPLOY if shared else ROOT / f"ansible/playbooks/apps/{app}.yml"
    return list(resolver.playbook(path))


def deploy_seed(app: str, steps: list[dict]) -> dict | None:
    """What playbooks/apps/deploy.yml knows before its tasks run, when the run goes through
    it: the app, its playbook directory and the role it derives from `deploy.role`."""
    shared = str(SHARED_DEPLOY.relative_to(ROOT))
    if not any(shared in (step.get("via") or []) for step in steps):
        return None
    return {"app": app, "playbook_dir": str(SHARED_DEPLOY.parent),
            "_deploy_role": (defaults(app).get("deploy") or {}).get("role", app)}


def deploy_reached(app: str, method: str, deep: bool = False, shared: bool = False):
    seed = deploy_seed(app, deploy_steps(app, None, shared))
    return reached(lambda r: deploy_steps(app, r, shared), method, deep, seed)


@lru_cache(maxsize=None)
def hook_steps(role: str, hook: str, render=None) -> list[dict]:
    resolver = task_parts.Resolver(ROOT, trace=True, render=render)
    return list(resolver.role(role, f"{hook}.yml", [], [],
                              ROOT / "ansible/playbooks/maintenance/x", 0))


def row(app: str, deep: bool = False) -> dict:
    """Backup is the role's hook, which Backup App runs on a guest; on Kubernetes it runs
    the CronJob Deploy created (`recovery-backup.yml` in the kubernetes Deploy list)."""
    data = defaults(app)
    hosting = data.get("hosting") or {}
    supported = sorted(hosting.get("supported") or [])
    methods = sorted({combo.split("-")[0] for combo in supported} | {hosting.get("method")})
    role = role_of(app)
    entry = {"method": hosting.get("method"), "supported": supported, "deploy": {}}
    for method in methods:
        entry["deploy"][method] = deploy_reached(app, method, deep)
    for hook in ("backup", "restore"):
        if not role or not (ROOT / f"ansible/roles/{role}/tasks/{hook}.yml").is_file():
            continue
        entry[hook] = {method: reached(lambda r: hook_steps(role, hook, r), method, deep)
                       for method in methods if hook == "restore" or method != "kubernetes"}
    return entry


def methods_of(app: str) -> list[str]:
    hosting = defaults(app).get("hosting") or {}
    return sorted({c.split("-")[0] for c in hosting.get("supported") or []}
                  | {hosting.get("method")})


def shared_deploy(app: str, deep: bool = False) -> dict:
    """The app's Deploy run through playbooks/apps/deploy.yml, as if its shim imported it."""
    return {"deploy": {method: deploy_reached(app, method, deep, shared=True)
                       for method in methods_of(app)}}


def table() -> dict:
    return {app: row(app) for app in apps()}


# Per method: shared files every Deploy must reach, and files it must not reach.
GUEST = ("tasks/stack/find-or-create-host.yml", "tasks/proxmox/provision-native-guest.yml",
         "tasks/guest-bootstrap.yml")
RULES = {
    "deploy": {
        None: (["tasks/apps/load-config.yml"], []),
        "docker": (["tasks/stack/find-or-create-host.yml", "tasks/guest-bootstrap.yml"],
                   ["tasks/proxmox/provision-native-guest.yml"]),
        "native": (["tasks/guest-bootstrap.yml"], ["tasks/stack/find-or-create-host.yml"]),
        "kubernetes": (["tasks/kubernetes/open-app-access.yml",
                        "tasks/kubernetes/apply-manifest.yml"], list(GUEST)),
    },
    "restore": {"kubernetes": (["tasks/kubernetes/recovery-restore.yml"], [])},
}


def rules(app: str, entry: dict) -> list[str]:
    """Properties every row must have, checked on its deep lists (`row(app, deep=True)`);
    each returned string is one failure."""
    failures = []
    for operation, by_method in RULES.items():
        for method, files in (entry.get(operation) or {}).items():
            for rule in (by_method.get(None), by_method.get(method)):
                required, forbidden = rule or ([], [])
                failures += [f"{app}: the {method} {operation} does not reach {f}"
                             for f in required if f not in files]
                failures += [f"{app}: the {method} {operation} reaches {f}"
                             for f in forbidden if f in files]
    return failures


def self_check() -> list[str]:
    """The decider and the rules catch what they exist to catch."""
    failures = []
    steps = [{"play": "p", "keywords": {"vars": {
        "_k8s": "{{ hostvars['localhost'].app_config.hosting.method == 'kubernetes' }}"}}}]
    for method, expected in (("kubernetes", False), ("docker", True)):
        decider = Decider(method, steps)
        if decider.runs({"task": {"when": "not (_k8s | bool)"}}) is not expected:
            failures.append(f"self-check: a method flag is not decided for {method}")
        if decider.runs({"task": {"when": "other_fact | bool"}}) is not True:
            failures.append("self-check: an undecided condition does not keep its task")
    bad = {"deploy": {"kubernetes": ["tasks/apps/load-config.yml",
                                     "tasks/stack/find-or-create-host.yml"]}}
    if len(rules("x", bad)) != 3:
        failures.append("self-check: the rules miss a guest provisioned on Kubernetes")
    nested = [{"task": {}, "scope": [], "via": [
        "ansible/playbooks/apps/x.yml", "ansible/tasks/kubernetes/apply-manifest.yml",
        "ansible/tasks/proxmox/provision-native-guest.yml"]}]
    if "tasks/proxmox/provision-native-guest.yml" not in reached(
            lambda r: nested, "kubernetes", deep=True):
        failures.append("self-check: a guest created inside a shared file is not seen")
    return failures


def differences(app: str, recorded, tree) -> list[str]:
    """One line per field or list that differs, naming what the tree adds and drops."""
    if recorded == tree:
        return []
    if not isinstance(recorded, dict) or not isinstance(tree, dict):
        return [f"{app}: in gate/app-matrix.yml {recorded is not None}, in the tree "
                f"{tree is not None}"]
    lines = []
    for key in dict.fromkeys([*recorded, *tree]):
        old, new = recorded.get(key), tree.get(key)
        if old == new:
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            for method in dict.fromkeys([*old, *new]):
                was, now = old.get(method), new.get(method)
                if was != now:
                    added = [f for f in now or [] if f not in (was or [])]
                    dropped = [f for f in was or [] if f not in (now or [])]
                    lines.append(f"{app} {key} {method}: adds {added}, drops {dropped}"
                                 + (", same files in another order" if not added
                                    and not dropped else ""))
        else:
            lines.append(f"{app} {key}: recorded {old}, tree {new}")
    return lines


def dump(data: dict) -> str:
    class Dumper(yaml.SafeDumper):
        pass

    Dumper.add_representer(list, lambda d, v: d.represent_sequence(
        "tag:yaml.org,2002:seq", v, flow_style=all(isinstance(i, str) for i in v)
        and len(", ".join(v)) < 70))
    return yaml.dump(data, Dumper=Dumper, sort_keys=False, width=100)


def preview(names: list[str]) -> int:
    """How each app's Deploy row would change if its shim imported deploy.yml."""
    recorded = yaml.safe_load(TABLE.read_text()) or {}
    for app in names or [a for a in apps() if a not in OWN_DEPLOY]:
        lines = differences(app, {"deploy": recorded[app]["deploy"]}, shared_deploy(app))
        print("\n".join(lines) if lines else f"{app}: same Deploy row")
    return 0


def main() -> int:
    if "--preview" in sys.argv:
        return preview(sys.argv[sys.argv.index("--preview") + 1:])
    current = table()
    if "--update" in sys.argv:
        print(dump(current), end="")
        return 0
    failures = self_check() + [f for app in current for f in rules(app, row(app, deep=True))]
    # deploy.yml holds to the rules for every app before any app's shim imports it.
    failures += [f"playbooks/apps/deploy.yml as {f}" for app in current
                 if app not in OWN_DEPLOY for f in rules(app, shared_deploy(app, deep=True))]
    recorded = yaml.safe_load(TABLE.read_text()) or {}
    for app in sorted(set(current) | set(recorded)):
        failures += differences(app, recorded.get(app), current.get(app))
    for failure in failures:
        print(failure)
    if failures:
        print("Fix the change, or if it is intended, record it with "
              "`python gate/test-app-matrix.py --update > gate/app-matrix.yml` and name it "
              "in the PR body.")
        return 1
    print(f"app matrix: {len(current)} apps match gate/app-matrix.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
