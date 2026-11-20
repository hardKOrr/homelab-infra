#!/usr/bin/env python3
"""Remove App skips the database drop only when the backend is gone from the registry and
from Proxmox; a backend Proxmox still reports by name or tag keeps the drop (and its
refusal in resolve-app-database.yml)."""

import json
import os
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
tasks = yaml.safe_load((ROOT / "ansible/tasks/removal/resolve-instance.yml").read_text())
names = [t.get("name") for t in tasks]
skip = tasks[names.index("Skip the drop of a database whose backend is already destroyed")]
read = tasks[names.index("Read Proxmox guests when the app's database backend is unregistered")]
assert names.index("Skip the drop of a database whose backend is already destroyed") < names.index(
    "Resolve the app's named database before unwiring")
assert "homelabinfra_infra.databases" in str(read["when"])

for guests, skipped in (([{"name": "k3s-1", "tags": "_+lab;_k3s"}, {"name": "other"}], True),
                        ([{"name": "postgresql-immich"}], False),
                        ([{"name": "renamed", "tags": "_+lab;_postgresql-immich"}], False)):
    variables = {"_rm_backend_guests": {"stdout": json.dumps(guests)},
                 "app_config": {"app": {"database": {"instance": "postgresql-immich"}}}}
    templar = Templar(loader=DataLoader(), variables=variables)
    result = all(templar.template("{{ " + c + " }}") for c in skip["when"])
    assert result is skipped, (guests, result)
print("remove: a database whose backend is destroyed is skipped, a live backend is not")
