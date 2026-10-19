#!/usr/bin/env python3
"""Keep Kometa's optional collection file in the operator-owned Secret."""

import os
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("ANSIBLE_CONFIG", str(ROOT / "ansible/ansible.cfg"))


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def render(value, variables):
    return Templar(loader=DataLoader(), variables=variables).template(value)


def test_collection_file_is_optional_and_validated():
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/kometa/tasks/main.yml").read_text(encoding="utf-8")
    )

    read = task_named(tasks, "Kometa | Read the optional operator-owned collection file")
    assert read["ansible.builtin.set_fact"]["_kometa_collections_yml"] == (
        "{{ app_config.app.collections_yml | default('', true) }}"
    )
    assert read["no_log"] is True

    validate = task_named(tasks, "Kometa | Validate the optional collection file as YAML")
    assert validate["when"] == "_kometa_collections_yml | length > 0"
    assert validate["no_log"] is True

    require = task_named(
        tasks, "Kometa | Require a collection mapping when a collection file is stored"
    )
    assert require["when"] == "_kometa_collections_yml | length > 0"
    assert require["no_log"] is True

    valid_yaml = "collections:\n  p2-canary:\n    plex_all: true\n"
    parse = task_named(tasks, "Kometa | Validate the optional collection file as YAML")
    expression = parse["ansible.builtin.set_fact"]["_kometa_collections_document"]
    document = render(
        {"document": expression}, {"_kometa_collections_yml": valid_yaml}
    )["document"]
    assert document["collections"]["p2-canary"]["plex_all"] is True


def test_secret_data_contains_only_config_when_collection_file_is_absent():
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/kometa/tasks/main.yml").read_text(encoding="utf-8")
    )
    task = task_named(tasks, "Kometa | Assemble the operator-owned configuration files")
    expression = task["ansible.builtin.set_fact"]["_kometa_secret_data"]
    rendered = render(
        {"secret_data": expression},
        {"_kometa_config_yml": "plex: {}\n", "_kometa_collections_yml": ""},
    )
    assert rendered["secret_data"] == {"config.yml": "plex: {}\n"}

    collection_text = "collections:\n  p2-canary:\n    plex_all: true\n"
    rendered = render(
        {"secret_data": expression},
        {
            "_kometa_config_yml": "plex: {}\n",
            "_kometa_collections_yml": collection_text,
        },
    )
    assert rendered["secret_data"] == {
        "config.yml": "plex: {}\n",
        "collections.yml": collection_text,
    }
    assert task["no_log"] is True


def render_cronjob(collections_yml):
    template = (ROOT / "ansible/roles/kometa/templates/manifest.yaml.j2").read_text(
        encoding="utf-8"
    )
    rendered = render(
        template,
        {
            "instance": "kometa",
            "app_config": {
                "app": {
                    "image": "kometateam/kometa:latest",
                    "schedule": "0 5 * * *",
                    "timeout_seconds": 7200,
                    "storage": 1,
                }
            },
            "homelabinfra_config": {"timezone": "UTC"},
            "_kometa_collections_yml": collections_yml,
        },
    )
    documents = list(yaml.safe_load_all(rendered))
    return next(document for document in documents if document["kind"] == "CronJob"), rendered


def test_manifest_mounts_optional_collection_file_read_only():
    cronjob, rendered = render_cronjob("")
    mounts = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0][
        "volumeMounts"
    ]
    secret_mounts = [mount for mount in mounts if "subPath" in mount]
    assert [mount["subPath"] for mount in secret_mounts] == ["config.yml"]

    collection_text = "collections:\n  p2-canary:\n    plex_all: true\n"
    cronjob, rendered = render_cronjob(collection_text)
    mounts = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0][
        "volumeMounts"
    ]
    secret_mounts = [mount for mount in mounts if "subPath" in mount]
    assert [mount["subPath"] for mount in secret_mounts] == [
        "config.yml",
        "collections.yml",
    ]
    assert secret_mounts[1]["mountPath"] == "/config/collections.yml"
    assert secret_mounts[1]["readOnly"] is True
    assert collection_text not in rendered


def test_on_demand_playbook_runs_the_deployed_kometa_cronjob():
    playbook = yaml.safe_load(
        (ROOT / "ansible/playbooks/maintenance/run-kometa.yml").read_text(
            encoding="utf-8"
        )
    )
    play = playbook[0]
    assert play["vars"]["instance"] == "kometa"
    run = task_named(play["tasks"], "Run the deployed Kometa CronJob")
    assert run["ansible.builtin.include_tasks"] == (
        "../../tasks/kubernetes/run-job.yml"
    )
    assert run["vars"]["k8s_job_name"] == "{{ instance }}-manual"
    assert run["vars"]["k8s_job_from_cronjob"] == "{{ instance }}"
    assert run["vars"]["k8s_job_timeout"] == 7200

    endpoint = task_named(play["pre_tasks"], "Check the configured Plex endpoint from the cluster")
    assert endpoint["ansible.builtin.uri"]["method"] == "GET"
    assert endpoint["ansible.builtin.uri"]["status_code"] == [200]
    assert endpoint["delegate_to"] == "{{ k8s_api_host }}"
    assert endpoint["no_log"] is True
    assert endpoint["register"] == "_kometa_plex_endpoint"
    assert endpoint["failed_when"] is False

    clean_log = task_named(play["tasks"], "Require a clean Kometa completion log")
    assert clean_log.get("no_log") is not True
    assert any("Finished" in item for item in clean_log["ansible.builtin.assert"]["that"])
    assert any("Config Error" in item for item in clean_log["ansible.builtin.assert"]["that"])
    assert any("Plex Error" in item for item in clean_log["ansible.builtin.assert"]["that"])


if __name__ == "__main__":
    test_collection_file_is_optional_and_validated()
    test_secret_data_contains_only_config_when_collection_file_is_absent()
    test_manifest_mounts_optional_collection_file_read_only()
    test_on_demand_playbook_runs_the_deployed_kometa_cronjob()
    print("ok Kometa optional collection Secret and manifest")
