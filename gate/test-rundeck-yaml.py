#!/usr/bin/env python3
"""Rendered Rundeck jobs and instance option publishing."""

import re
import subprocess
import sys
import tempfile
from pathlib import Path
import yaml

repo = Path(__file__).resolve().parents[1]


def check_jobs_and_instances(work):
    import pathlib, subprocess, sys
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "render_job", repo / "rundeck" / "render-job.py"
    )
    render_job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render_job)
    multi_estate = work / "infrastructure.yml"
    multi_estate.write_text(
        "domains:\n  personal:\n    domain: personal.example.test\n    default: true\n  foxglove:\n    domain: foxglove.example.test\n",
        encoding="utf-8",
    )
    original_config = render_job.INFRASTRUCTURE_CONFIG
    original_render = lambda source: render_with_config(source, original_config)

    def render_with_config(source, config):
        current = render_job.INFRASTRUCTURE_CONFIG
        try:
            render_job.INFRASTRUCTURE_CONFIG = config
            return render_job.render(source)
        finally:
            render_job.INFRASTRUCTURE_CONFIG = current

    render_job.INFRASTRUCTURE_CONFIG = multi_estate
    deploy_radarr = render_job.render(repo / "rundeck" / "jobs" / "deploy-radarr.yaml")[
        0
    ]
    radarr_instance = next(
        (option for option in deploy_radarr["options"] if option["name"] == "instance")
    )
    assert (
        radarr_instance["value"] == "radarr-personal"
    ), "radarr_instance['value'] == 'radarr-personal'"
    assert radarr_instance["valuesUrl"].endswith(
        "/radarr.json"
    ), "radarr_instance['valuesUrl'].endswith('/radarr.json')"
    for slug in render_job.load_applications():
        routing = render_job.app_defaults_of(slug).get("routing") or {}
        assert not routing.get(
            "estate"
        ), f"app-defaults/{slug}.yml declares routing.estate — estate names belong to one lab"
    defaults_dir = repo / "ansible" / "vars" / "app-defaults"
    for slug, app in render_job.load_applications().items():
        defaults = render_job.app_defaults_of(slug)
        routing = defaults.get("routing") or {}
        if routing.get("proxy", "none") == "none":
            continue
        assert routing.get(
            "subdomain"
        ), f"app-defaults/{slug}.yml declares no routing.subdomain, so its hostname would follow the instance name"
    catalog_text = (repo / "catalog" / "applications.yml").read_text(encoding="utf-8")
    try:
        render_job.load_document
        original = render_job.APPLICATION_CATALOG
        unscoped = work / "unscoped-catalog.yml"
        unscoped.write_text(
            catalog_text.replace("    scope: estate\n", "\n", 1), encoding="utf-8"
        )
        render_job.APPLICATION_CATALOG = unscoped
        try:
            render_job.load_applications()
        except ValueError as error:
            assert "scope must be declared" in str(error), str(error)
        else:
            raise AssertionError("an application with no scope was accepted")
    finally:
        render_job.APPLICATION_CATALOG = original
    configure_radarr = next(
        (
            job
            for job in render_job.render(
                repo / "rundeck" / "jobs" / "configure-app.yaml"
            )
            if job["name"] == "Configure Radarr"
        )
    )
    estate_option = next(
        (option for option in configure_radarr["options"] if option["name"] == "estate")
    )
    assert estate_option["required"] is True, "estate_option['required'] is True"
    assert estate_option["value"] == "personal", "estate_option['value'] == 'personal'"
    assert estate_option["enforced"] is True, "estate_option['enforced'] is True"
    configure_caddy = next(
        (
            job
            for job in render_job.render(
                repo / "rundeck" / "jobs" / "configure-app.yaml"
            )
            if job["name"] == "Configure Caddy"
        )
    )
    assert all(
        (option["name"] != "estate" for option in configure_caddy["options"])
    ), "all((option['name'] != 'estate' for option in configure_caddy['options']))"
    rollback_names = {
        job["name"]
        for job in render_job.render(
            repo / "rundeck" / "jobs" / "rollback-container.yaml"
        )
    }
    assert "Rollback Radarr" in rollback_names, "'Rollback Radarr' in rollback_names"
    assert (
        "Rollback Authentik" not in rollback_names
    ), "'Rollback Authentik' not in rollback_names"
    assert (
        "Rollback Observability" not in rollback_names
    ), "'Rollback Observability' not in rollback_names"
    rollback_radarr = next(
        (
            job
            for job in render_job.render(
                repo / "rundeck" / "jobs" / "rollback-container.yaml"
            )
            if job["name"] == "Rollback Radarr"
        )
    )
    rollback_script = rollback_radarr["sequence"]["commands"][0]["script"]
    assert (
        "app=radarr" in rollback_script and "stack=" not in rollback_script
    ), "'app=radarr' in rollback_script and 'stack=' not in rollback_script"
    multi_estate.write_text(
        "domains:\n  personal: {domain: personal.example.test}\n  foxglove: {domain: foxglove.example.test}\n",
        encoding="utf-8",
    )
    try:
        render_job.estate_context()
    except ValueError as error:
        assert "exactly one default: true" in str(
            error
        ), "'exactly one default: true' in str(error)"
    else:
        raise AssertionError(
            "multi-estate rendering accepted an order-dependent default"
        )
    multi_estate.write_text(
        "domains:\n  personal:\n    domain: personal.example.test\n    default: true\n  foxglove:\n    domain: foxglove.example.test\n",
        encoding="utf-8",
    )
    failures = []
    for path in sorted((repo / "rundeck" / "jobs").glob("*.yaml")):
        rendered = render_job.render(path)
        assert rendered, f"{path.name}: renderer returned an empty document"
        for job in rendered:
            for key in ("name", "group"):
                if "%" in str(job.get(key, "")):
                    failures.append(
                        f"{path.name}: {job.get('name')} kept a placeholder in {key}"
                    )
            for index, command in enumerate(
                job.get("sequence", {}).get("commands", [])
            ):
                script = command.get("script")
                if not script:
                    continue
                if re.search("%[A-Z_]+%", script):
                    failures.append(
                        f"{path.name}: {job['name']} kept a placeholder in its script"
                    )
                target = work / f"{job['uuid']}-{index}.sh"
                with open(target, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(script)
                result = subprocess.run(
                    ["bash", "-n", str(target)], capture_output=True, text=True
                )
                if result.returncode != 0:
                    failures.append(
                        f"{path.name}: {job['name']} step {index}: {result.stderr.strip()}"
                    )
    if failures:
        print("\n".join((f"ERROR: {failure}" for failure in failures)), file=sys.stderr)
        sys.exit(1)
    for name in (
        "configure-app.yaml",
        "remove-app.yaml",
        "restart-app.yaml",
        "rollback-container.yaml",
        "tail-applog.yaml",
    ):
        source = repo / "rundeck/jobs" / name
        output = subprocess.check_output(
            [sys.executable, str(repo / "rundeck/render-job.py"), str(source)],
            text=True,
        )
        assert yaml.safe_load(output) == original_render(source), name
        assert not any(
            (isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(output))
        ), name
    import contextlib, importlib.util, io
    from unittest.mock import patch

    spec = importlib.util.spec_from_file_location(
        "app_instances", repo / "ansible" / "scripts" / "app-instances.py"
    )
    app_instances = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app_instances)
    applications = app_instances.load_applications(
        repo / "catalog" / "applications.yml"
    )
    estates = app_instances.load_estates(work / "infrastructure.yml")
    fixture = work / "fixture"
    (fixture / "config" / "apps").mkdir(parents=True)
    (fixture / "config" / "apps" / "radarr-personal.yml").write_text(
        "routing:\n  estate: personal\n", encoding="utf-8"
    )
    (fixture / "config" / "apps" / "radarr-foxglove-4k.yml").write_text(
        "routing:\n  estate: foxglove\n", encoding="utf-8"
    )
    instances, unmatched, invalid = app_instances.collect(
        fixture, applications, estates
    )
    assert not unmatched and (not invalid), "not unmatched and (not invalid)"
    assert {entry["value"] for entry in instances["radarr"]} == {
        "radarr-personal",
        "radarr-foxglove-4k",
    }, "{entry['value'] for entry in instances['radarr']} == {'radarr-personal', 'radarr-foxglove-4k'}"
    assert {entry["name"] for entry in instances["radarr"]} == {
        "radarr-personal — personal",
        "radarr-foxglove-4k — foxglove",
    }, "{entry['name'] for entry in instances['radarr']} == {'radarr-personal — personal', 'radarr-foxglove-4k — foxglove'}"
    (fixture / "config" / "apps" / "radarr.yml").write_text("{}\n", encoding="utf-8")
    _, _, invalid = app_instances.collect(fixture, applications, estates)
    assert (
        invalid and "radarr-personal" in invalid[0]
    ), "invalid and 'radarr-personal' in invalid[0]"
    error_target = work / "instances" / "radarr.json"
    error_output = io.StringIO()
    with patch.object(
        app_instances.Path,
        "write_text",
        side_effect=PermissionError(13, "Permission denied"),
    ), contextlib.redirect_stderr(error_output):
        assert not app_instances.write_if_changed(
            error_target, '["radarr"]\n'
        ), "not app_instances.write_if_changed(error_target, '[\"radarr\"]\\n')"
    assert (
        "app-instances: cannot write" in error_output.getvalue()
    ), "'app-instances: cannot write' in error_output.getvalue()"
    assert (
        "Traceback" not in error_output.getvalue()
    ), "'Traceback' not in error_output.getvalue()"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        check_jobs_and_instances(Path(directory))
    print("Rundeck YAML: jobs, aliases, estate options and instance publisher passed")
