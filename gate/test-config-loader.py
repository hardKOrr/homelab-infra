#!/usr/bin/env python3
"""Every deploy playbook's merged app config equals the documented merge.

For each `ansible/playbooks/apps/<app>.yml` that loads an app config, the suite takes the
config-loading tasks of its first play (`tasks/apps/load-config.yml` and its inputs, or
the hand-written loads it replaced; for a shim, those of the playbook it imports), runs
them with Ansible against fixture inputs, and compares the result with the merge in
`ansible/vars/contract/app-layering.md`: the shared base defaults, the app's defaults,
the platform layer (the `infrastructure.yml` keys the defaults name in `platform_layers`,
then the loader's `load_config_layer`), the instance file, then the instance's Vaultwarden item under `app`
(only for the hosting methods the playbook or the defaults' `vault.methods` name). Each app
runs twice: on its default hosting method, and on Kubernetes too where the Vaultwarden
overlay depends on the method.

`--dump FILE` writes every app's merged config as JSON instead of comparing, so a change
to the loader can be checked by dumping before and after it and diffing the two files.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

REPO = Path(__file__).resolve().parents[1]
APPS = REPO / "ansible/playbooks/apps"
NOT_DEPLOY = {"deploy", "remove", "remove-k3s-cluster", "migrate-servarr", "_template"}
LOADER = "tasks/apps/load-config.yml"
BASE = yaml.safe_load((REPO / "ansible/vars/app-defaults/_base.yml").read_text())["_base_defaults"]
INFRA = {"media_storage": {"library": "/gate/library"},
         "frigate_storage": {"path": "/gate/recordings"}}


def is_load_step(task: dict) -> bool:
    text = json.dumps(task)
    if LOADER in text:
        return True
    module = task.get("ansible.builtin.include_vars") or {}
    if isinstance(module, dict) and ("app-defaults/" in str(module.get("file"))
                                     or "config/apps/" in str(module.get("file"))):
        return True
    facts = task.get("ansible.builtin.set_fact") or {}
    merged = " ".join(str(v) for v in facts.values()) if isinstance(facts, dict) else ""
    return any(name in merged for name in ("_instance_config", "_pdm_instance",
                                           "homelabinfra_vault.apps"))


def result_fact(steps: list[dict]) -> str:
    for task in steps:
        if task.get("ansible.builtin.set_fact", {}).keys() == {"app"}:
            continue
        inputs = task.get("vars") or {}
        if LOADER in json.dumps(task):
            return inputs.get("load_config_fact", "app_config")
        facts = task.get("ansible.builtin.set_fact") or {}
        if isinstance(facts, dict) and facts:
            return next(iter(facts))
    raise AssertionError("no merged config fact")


def loading_steps(path: Path) -> list[dict] | None:
    """The first play's config-loading steps; a shim's are those of the playbook it imports,
    run with the shim's vars (`app`) set first."""
    plays = yaml.safe_load(path.read_text())
    first = plays[0]
    target = first.get("ansible.builtin.import_playbook") or first.get("import_playbook")
    if target:
        steps = loading_steps(path.parent / target) or []
        return [{"ansible.builtin.set_fact": first.get("vars") or {}}, *steps] if steps else None
    steps = [t for t in first.get("pre_tasks") or [] if is_load_step(t)]
    return steps or None


def instance_file(app: str, kubernetes: bool) -> dict:
    example = REPO / f"config.example/apps/{app}.example.yml"
    doc = yaml.safe_load(example.read_text()) if example.exists() else None
    doc = doc or {}
    doc = copy.deepcopy(doc)
    for key in ("app", "routing", "hosting"):
        if not isinstance(doc.get(key), dict):
            doc[key] = {}
    doc["app"]["gate_instance_key"] = app
    doc["routing"]["subdomain"] = f"gate-{app}"
    if kubernetes:
        doc["hosting"]["method"] = "kubernetes"
    return doc


def vault_methods(app: str, steps: list[dict]):
    """The hosting methods that take the Vaultwarden item; None for every method."""
    loader = next((t for t in steps if LOADER in json.dumps(t)), None)
    methods = ((loader or {}).get("vars") or {}).get("load_config_vault_methods")
    return methods if methods is not None else (defaults_of(app).get("vault") or {}).get("methods")


def defaults_of(app: str) -> dict:
    path = REPO / f"ansible/vars/app-defaults/{app}.yml"
    return yaml.safe_load(path.read_text())[app.replace("-", "_") + "_defaults"]


def variants(app: str, steps: list[dict], dump: str | None) -> tuple[bool, ...]:
    """Kubernetes as well as the default method, when the vault overlay depends on it."""
    return (False, True) if dump or vault_methods(app, steps) else (False,)


def merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for key, value in over.items():
            out[key] = merge(base[key], value) if key in base else value
        return out
    return over


def main() -> int:
    dump = sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv else None
    targets = {}
    for path in sorted(APPS.glob("*.yml")):
        if path.stem in NOT_DEPLOY:
            continue
        steps = loading_steps(path)
        assert steps, f"{path.name}: no config-loading steps in its first play"
        targets[path.stem] = steps

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "ansible/playbooks/apps").mkdir(parents=True)
        for name in ("vars", "tasks"):
            (root / "ansible" / name).symlink_to(REPO / "ansible" / name)
        (root / "config/apps").mkdir(parents=True)
        out = root / "out"
        out.mkdir()
        vault = {"apps": {}}
        runs = []
        for app, steps in targets.items():
            for kubernetes in variants(app, steps, dump):
                instance = f"{app}-k8s" if kubernetes else app
                (root / f"config/apps/{instance}.yml").write_text(
                    yaml.safe_dump(instance_file(app, kubernetes)))
                vault["apps"][instance] = {"gate_vault_key": instance}
                runs.append({"app": app, "instance": instance})
            fact = result_fact(steps)
            (root / f"ansible/playbooks/apps/gate-{app}.yml").write_text(yaml.safe_dump([
                *steps,
                {"ansible.builtin.copy": {"content": "{{ " + fact + " | to_json }}",
                                          "dest": str(out) + "/{{ instance }}.json"}},
            ], sort_keys=False))
        playbook = root / "ansible/playbooks/apps/gate-config-loader.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "tasks": [
                {"ansible.builtin.set_fact": {
                    "homelabinfra_config": {"infrastructure": INFRA},
                    "homelabinfra_vault": vault,
                    "_luv_vars_file": str(root / "ansible/vars/homelabinfra-defaults.yml"),
                    "_luv_config_dir": str(root / "config"),
                }},
                {"ansible.builtin.include_tasks": "gate-{{ run.app }}.yml",
                 "vars": {"instance": "{{ run.instance }}"},
                 "loop": runs, "loop_control": {"loop_var": "run"}},
            ],
        }], sort_keys=False))
        env = dict(os.environ, ANSIBLE_CONFIG=str(REPO / "ansible/ansible.cfg"),
                   ANSIBLE_INVENTORY=str(REPO / "gate/fixtures/localhost.ini"))
        player = os.environ.get("GATE_ANSIBLE_PLAYBOOK",
                                str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"))
        run = subprocess.run([player, "-i", "localhost,", "-c", "local", str(playbook)],
                             capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        if run.returncode != 0:
            print(run.stdout[-6000:], run.stderr[-3000:])
            return 1

        results = {}
        failures = []
        templar = Templar(loader=DataLoader(),
                          variables={"homelabinfra_config": {"infrastructure": INFRA}})
        for app, steps in targets.items():
            defaults = merge(BASE, defaults_of(app))
            loader = next((t for t in steps if LOADER in json.dumps(t)), None)
            inputs = (loader or {}).get("vars") or {}
            for kubernetes in variants(app, steps, dump):
                instance = f"{app}-k8s" if kubernetes else app
                got = json.loads((out / f"{instance}.json").read_text())
                results[instance] = got
                if loader is None:
                    continue
                platform = {key: INFRA.get(key, {}) for key in defaults.get("platform_layers", [])}
                layer = merge(platform, templar.template(inputs.get("load_config_layer", {})))
                want = merge(merge(defaults, layer), instance_file(app, kubernetes))
                methods = vault_methods(app, steps)
                method = (want.get("hosting") or {}).get("method", "")
                if methods is None or method in methods:
                    want = merge(want, {"app": {"gate_vault_key": instance}})
                templar.available_variables = {"homelabinfra_config": {"infrastructure": INFRA},
                                               "instance": instance}
                want = templar.template(want)
                if got != want:
                    failures.append(f"{instance}: loader gave\n{json.dumps(got, sort_keys=True)}\n"
                                    f"documented merge is\n{json.dumps(want, sort_keys=True)}")
        if dump:
            Path(dump).write_text(json.dumps(results, indent=1, sort_keys=True))
            print(f"wrote {len(results)} merged configs to {dump}")
            return 0
        unconverted = [app for app, steps in targets.items()
                       if not any(LOADER in json.dumps(t) for t in steps)]
        if unconverted:
            failures.append("load their config by hand instead of " + LOADER + ": "
                            + ", ".join(unconverted))
        for failure in failures:
            print("FAIL", failure)
        if not failures:
            print(f"ok: {len(results)} merged configs match the documented merge")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
