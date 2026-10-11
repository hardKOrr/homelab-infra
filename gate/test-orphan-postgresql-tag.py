#!/usr/bin/env python3
"""Exercise PostgreSQL orphan-tag repair against socket-free PVE fixtures."""

import copy
import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCE = yaml.safe_load(
    (ROOT / "ansible/playbooks/maintenance/repair-orphan-postgresql-tag.yml").read_text()
)[0]
FIXTURE = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
from pathlib import Path
root=Path(os.environ['REPAIR_FIXTURE']);args=sys.argv[1:]
if args[:2]==['get','/cluster/resources']:
 print(json.dumps(json.loads((root/'inventory.json').read_text())));sys.exit(0)
vmid=args[1].split('/')[-2];path=root/(vmid+'.json');cfg=json.loads(path.read_text())
if args[0]=='get':print(json.dumps(cfg));sys.exit(0)
if args[0]=='set':
 assert args[2]=='--tags' and args[4]=='--digest' and cfg['digest']==args[5]
 cfg['tags']=args[3];cfg['digest']=hashlib.sha256(args[3].encode()).hexdigest()
 path.write_text(json.dumps(cfg));(root/'writes').open('a').write(vmid+'\n');sys.exit(0)
raise SystemExit(2)
'''


def adapt(value, root):
    if isinstance(value, dict):
        return {key: adapt(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [adapt(item, root) for item in value]
    if isinstance(value, str):
        return value.replace("/root/homelab-infra/tag-repair", str(root / "recovery"))
    return value


def run_case(root, binary, case):
    directory = root / case
    directory.mkdir()
    (directory / "pvesh").symlink_to(binary)
    config = {
        "hostname": "postgresql-immich",
        "tags": "_+lab;_-debian",
        "description": "operator notes",
        "net0": "name=eth0,ip=192.0.2.10/24,hwaddr=00:11:22:33:44:55",
        "rootfs": "fixture-storage:vm-123-disk-0,size=32G",
        "digest": "0123456789abcdef",
    }
    if case == "repeat":
        config["tags"] += ";_postgresql-immich"
    inventory = [
        {
            "vmid": 123,
            "node": "localhost",
            "name": "postgresql-immich",
            "type": "lxc",
            "tags": config["tags"],
        }
    ]
    if case == "unowned":
        config["tags"] = inventory[0]["tags"] = "_-debian"
    if case == "wrong_type":
        inventory[0]["type"] = "qemu"
    if case == "template":
        inventory[0]["template"] = 1
    if case == "duplicate_vmid":
        inventory.append({**inventory[0], "node": "other-node"})
    if case == "duplicate_name":
        inventory.append({**inventory[0], "vmid": 124})
    if case == "missing":
        inventory.clear()
    if case == "wrong_hostname":
        config["hostname"] = "other-instance"
    if case == "locked":
        config["lock"] = "backup"
    if case == "missing_digest":
        del config["digest"]
    before = copy.deepcopy(config)
    (directory / "123.json").write_text(json.dumps(config))
    (directory / "inventory.json").write_text(json.dumps(inventory))

    play = adapt(copy.deepcopy(SOURCE), directory)
    play.pop("pre_tasks")
    play["connection"] = "local"
    play["vars"].update(
        {
            "homelabinfra_config": {"proxmox": {"node": "localhost"}},
            "repair_instance": "postgresql-immich",
            "repair_vmid": 123,
            "repair_execute": case != "plan",
            "repair_confirmation": (
                "WRONG"
                if case == "wrong_confirmation"
                else "RESTORE POSTGRESQL INSTANCE TAG _postgresql-immich"
            ),
        }
    )
    path = directory / "play.yml"
    path.write_text(yaml.safe_dump([play], sort_keys=False))
    env = dict(
        os.environ,
        REPAIR_FIXTURE=str(directory),
        PATH=str(directory) + os.pathsep + os.environ["PATH"],
        ANSIBLE_CONFIG=str(ROOT / "ansible/ansible.cfg"),
        ANSIBLE_NOCOLOR="1",
    )
    ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
    result = subprocess.run(
        [str(ansible), "-i", "localhost,", str(path)],
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    succeeds = case in {"plan", "execute", "repeat"}
    assert (result.returncode == 0) == succeeds, (
        case,
        result.stdout,
        result.stderr,
    )
    after = json.loads((directory / "123.json").read_text())
    writes = (directory / "writes").read_text() if (directory / "writes").exists() else ""
    if case == "execute":
        assert writes == "123\n"
        expected = copy.deepcopy(before)
        expected["tags"] += ";_postgresql-immich"
        expected["digest"] = after["digest"]
        assert after == expected, after
        recovery = next((directory / "recovery").glob("*.json"))
        assert json.loads(recovery.read_text()) == before
    elif case == "repeat":
        assert after == before and not writes
    else:
        assert after == before and not writes, (case, after)
    print(f"{case}: PASS")


def main():
    cases = [
        "plan",
        "execute",
        "repeat",
        "missing",
        "duplicate_vmid",
        "duplicate_name",
        "unowned",
        "wrong_type",
        "template",
        "wrong_hostname",
        "locked",
        "missing_digest",
        "wrong_confirmation",
    ]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        binary = root / "pvesh"
        binary.write_text(FIXTURE)
        binary.chmod(0o700)
        for case in cases:
            run_case(root, binary, case)
    print("orphan PostgreSQL instance tag repair tests passed")


if __name__ == "__main__":
    main()
