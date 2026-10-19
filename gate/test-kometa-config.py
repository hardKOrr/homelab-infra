#!/usr/bin/env python3
"""Keep Kometa's optional collection file in the operator-owned Secret."""

import os
import re
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

    parse = task_named(tasks, "Kometa | Validate the optional collection file as YAML")
    expression = parse["ansible.builtin.set_fact"]["_kometa_collections_document"]
    collections_yaml = "collections:\n  p2-canary:\n    plex_all: true\n"
    dynamic_yaml = "dynamic_collections:\n  p2-dynamic:\n    type: decade\n"
    documents = [
        render({"document": expression}, {"_kometa_collections_yml": value})[
            "document"
        ]
        for value in (collections_yaml, dynamic_yaml)
    ]
    assert documents[0]["collections"]["p2-canary"]["plex_all"] is True
    assert documents[1]["dynamic_collections"]["p2-dynamic"]["type"] == "decade"

    require = task_named(
        tasks, "Kometa | Require a collection mapping when a collection file is stored"
    )
    conditions = require["ansible.builtin.assert"]["that"]

    def valid_document(document):
        return all(
            str(
                render(
                    {"valid": "{{ " + condition + " }}"},
                    {"_kometa_collections_document": document},
                )["valid"]
            ).lower()
            == "true"
            for condition in conditions
        )

    assert valid_document(documents[0])
    assert valid_document(documents[1])
    assert not valid_document({"collections": {}, "dynamic_collections": {}})
    assert not valid_document({"collections": [], "dynamic_collections": {}})


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

    merge = task_named(play["pre_tasks"], "Merge Kometa config and Vaultwarden values")
    merge_expression = merge["ansible.builtin.set_fact"]["app_config"]
    assert "_app_defaults.kometa_defaults" in merge_expression
    assert "_instance_config | default({})" in merge_expression
    assert "homelabinfra_vault.apps | default({})" in merge_expression
    config = task_named(
        play["pre_tasks"], "Read the operator-owned Kometa configuration"
    )
    assert "app_config.app.config_yml | from_yaml" in config["ansible.builtin.set_fact"][
        "_kometa_config_document"
    ]

    run = task_named(play["tasks"], "Run the deployed Kometa CronJob")
    assert run["ansible.builtin.include_tasks"] == (
        "../../tasks/kubernetes/run-job.yml"
    )
    assert run["vars"]["k8s_job_name"] == "{{ instance }}-manual"
    assert run["vars"]["k8s_job_from_cronjob"] == "{{ instance }}"
    assert run["vars"]["k8s_job_timeout"] == 7200

    endpoint = task_named(
        play["pre_tasks"], "Check the configured Plex endpoint from the cluster"
    )
    assert endpoint["ansible.builtin.uri"]["method"] == "GET"
    assert endpoint["ansible.builtin.uri"]["status_code"] == [200]
    assert "_kometa_config_document.plex.url" in endpoint["ansible.builtin.uri"]["url"]
    assert endpoint["ansible.builtin.uri"]["headers"]["X-Plex-Token"] == (
        "{{ _kometa_config_document.plex.token }}"
    )
    assert endpoint["delegate_to"] == "{{ k8s_api_host }}"
    assert endpoint["no_log"] is True
    assert endpoint["register"] == "_kometa_plex_endpoint"
    assert endpoint["failed_when"] is False
    assert "loop" not in endpoint
    runbook_text = (ROOT / "ansible/playbooks/maintenance/run-kometa.yml").read_text(
        encoding="utf-8"
    )
    assert "plex_endpoint_candidates" not in runbook_text

    reachability = task_named(
        play["pre_tasks"], "Require the configured Plex endpoint to be reachable"
    )
    assert reachability["ansible.builtin.assert"]["that"] == (
        "_kometa_plex_endpoint.status | default(0) | int == 200"
    )

    clean_log = task_named(play["tasks"], "Require a clean Kometa completion log")
    assert clean_log.get("no_log") is not True
    conditions = clean_log["ansible.builtin.assert"]["that"]
    finished_condition = next(item for item in conditions if "Finished" in item)
    finished_pattern = re.search(
        r"search\(\s*'([^']+)'\s*\)", finished_condition, re.DOTALL
    ).group(1).replace("\\\\", "\\")
    error_condition = next(item for item in conditions if "ERROR|CRITICAL|FATAL" in item)
    error_pattern = re.search(
        r"search\(\s*'([^']+)'\s*\)", error_condition, re.DOTALL
    ).group(1).replace("\\\\", "\\")
    assert "(?i" not in error_pattern

    # Captured from the successful live Job log, with generated pod/container IDs
    # anonymized. That healthy run had no ERROR-level record to use as a fixture.
    live_job_log_line = (
        "[pod/<job-pod>/<container>] |                                            Finished"
    )
    assert re.search(finished_pattern, live_job_log_line)
    assert not re.search(error_pattern, live_job_log_line)

    # Exercise Kometa's bracketed severity field after the same kubectl prefix seen
    # in the live log. Keep the older pipe-delimited format covered as well.
    pod_log_prefix = "[pod/<job-pod>/<container>] "
    kometa_error_lines = [
        pod_log_prefix + "[2026-01-01 00:00:00,000] [builder.py:197] [ERROR]     | Plex failed",
        pod_log_prefix + "[2026-01-01 00:00:00,001] [builder.py:198] [CRITICAL] | Invalid config",
        pod_log_prefix + "[2026-01-01 00:00:00,002] [kometa.py:299] [FATAL]     | Fatal config error",
        pod_log_prefix + "| 2026-01-01 00:00:00 | ERROR    | Plex failed |",
    ]
    assert all(re.search(error_pattern, line) for line in kometa_error_lines)

    info_log_with_matching_titles = "\n".join(
        pod_log_prefix + line
        for line in [
            "[2026-01-01 00:00:00,000] [builder.py:197] [INFO] | Collection: Critical Summary",
            "[2026-01-01 00:00:00,001] [builder.py:198] [INFO] | Item: Plex Error",
            "[2026-01-01 00:00:00,002] [builder.py:199] [INFO] | Title: FATAL",
            "[2026-01-01 00:00:00,003] [builder.py:200] [INFO] | Item title: [ERROR] |",
        ]
    )
    assert not re.search(error_pattern, info_log_with_matching_titles)


if __name__ == "__main__":
    test_collection_file_is_optional_and_validated()
    test_secret_data_contains_only_config_when_collection_file_is_absent()
    test_manifest_mounts_optional_collection_file_read_only()
    test_on_demand_playbook_runs_the_deployed_kometa_cronjob()
    print("ok Kometa optional collection Secret and manifest")
