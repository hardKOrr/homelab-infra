#!/usr/bin/env python3
"""Render a classified Rundeck job with the secure options its execution path needs."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


PROJECT = "homelab-infra"
BASE = f"keys/project/{PROJECT}"
REPO_ROOT = Path(__file__).resolve().parent.parent
APPLICATION_CATALOG = REPO_ROOT / "catalog" / "applications.yml"
JOB_GROUPS = REPO_ROOT / "rundeck" / "job-groups.yml"


def secure_option(name: str, path: str, description: str, *, required: bool = True) -> dict:
    return {
        "name": name,
        "description": description,
        "required": required,
        "secure": True,
        "valueExposed": True,
        "storagePath": path,
    }


BW_OPTIONS = [
    secure_option("bw_clientid", f"{BASE}/vaultwarden-machine/client-id", "Vault automation API client ID"),
    secure_option("bw_clientsecret", f"{BASE}/vaultwarden-machine/client-secret", "Vault automation API client secret"),
    secure_option("bw_password", f"{BASE}/vaultwarden-machine/master-password", "Vault automation master password"),
]
BW_OPTIONAL = [option | {"required": False} for option in BW_OPTIONS]
ADMIN_OPTION = secure_option(
    "vaultwarden_admin_token",
    f"{BASE}/vaultwarden-machine/admin-token",
    "Vaultwarden server-administration token",
)
RUNDECK_OPTION = secure_option(
    "rundeck_api_token",
    f"{BASE}/rundeck/api-token",
    "Rundeck control-plane API token",
)
CLOUDFLARE_OPTION = secure_option(
    "cloudflare_api_token",
    f"{BASE}/bootstrap/cloudflare-api-token",
    "Cloudflare token scoped to Zone Read and DNS Edit for the lab zone",
    required=False,
)


def load_mapping(path: Path, key: str) -> dict:
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError(f"{path}: expected schema_version: 1")
    mapping = document.get(key)
    if not isinstance(mapping, dict):
        raise ValueError(f"{path}: {key} must be a mapping")
    return mapping


def expected_groups() -> tuple[dict[str, str], dict[str, str]]:
    applications = load_mapping(APPLICATION_CATALOG, "applications")
    groups: dict[str, str] = {}
    application_names: dict[str, str] = {}

    for slug, application in applications.items():
        if not isinstance(application, dict):
            raise ValueError(f"{APPLICATION_CATALOG}: applications.{slug} must be a mapping")
        required = ("name", "job", "category", "type")
        missing = [field for field in required if not application.get(field)]
        if missing:
            raise ValueError(
                f"{APPLICATION_CATALOG}: applications.{slug} is missing {', '.join(missing)}"
            )
        job = application["job"]
        category = application["category"]
        app_type = application["type"]
        if not all(isinstance(value, str) for value in (job, category, app_type, application["name"])):
            raise ValueError(f"{APPLICATION_CATALOG}: applications.{slug} fields must be strings")
        if "/" in category or "/" in app_type:
            raise ValueError(f"{APPLICATION_CATALOG}: category and type must be one group segment")
        if job in groups:
            raise ValueError(f"{APPLICATION_CATALOG}: duplicate job {job}")
        groups[job] = f"Applications/{category}/{app_type}"
        application_names[job] = application["name"]

    classified = load_mapping(JOB_GROUPS, "jobs")
    for job, group in classified.items():
        if not isinstance(job, str) or not isinstance(group, str) or not group:
            raise ValueError(f"{JOB_GROUPS}: job names and groups must be non-empty strings")
        if job in groups:
            raise ValueError(f"{JOB_GROUPS}: {job} is also classified in the application catalog")
        groups[job] = group

    return groups, application_names


def load_job(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        jobs = yaml.safe_load(handle)
    if not isinstance(jobs, list) or len(jobs) != 1 or not isinstance(jobs[0], dict):
        raise ValueError(f"{path}: Rundeck job document must contain exactly one job")
    return jobs


def check_tree(job_directory: Path) -> None:
    groups, application_names = expected_groups()
    actual_files = {path.name for path in job_directory.glob("*.yaml")}
    expected_files = set(groups)
    missing = sorted(expected_files - actual_files)
    unexpected = sorted(actual_files - expected_files)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing jobs: {', '.join(missing)}")
        if unexpected:
            details.append(f"unclassified jobs: {', '.join(unexpected)}")
        raise ValueError(f"{job_directory}: {'; '.join(details)}")

    for filename in sorted(expected_files):
        job = load_job(job_directory / filename)[0]
        if job.get("group") != groups[filename]:
            raise ValueError(
                f"{filename}: group is {job.get('group')!r}; expected {groups[filename]!r}"
            )
        if filename in application_names and job.get("name") != f"Deploy {application_names[filename]}":
            raise ValueError(
                f"{filename}: name is {job.get('name')!r}; "
                f"expected 'Deploy {application_names[filename]}'"
            )


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        try:
            check_tree(Path(sys.argv[2]))
        except (OSError, ValueError, yaml.YAMLError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("Rundeck job tree: ok")
        return 0
    if len(sys.argv) != 2:
        print("usage: render-job.py JOB.yaml | render-job.py --check JOB_DIRECTORY", file=sys.stderr)
        return 2
    job_path = Path(sys.argv[1])
    try:
        groups, _ = expected_groups()
        jobs = load_job(job_path)
        expected_group = groups[job_path.name]
    except KeyError:
        raise SystemExit(f"{job_path.name}: job is not classified")
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise SystemExit(f"ERROR: {error}")

    for job in jobs:
        # Group is a projection of the repository-owned classifications. The
        # source value remains for reviewability and --check rejects drift.
        job["group"] = expected_group
        scripts = "\n".join(
            command.get("script", "")
            for command in job.get("sequence", {}).get("commands", [])
            if isinstance(command, dict)
        )
        name = job.get("name", "")
        additions: list[dict] = []
        if name == "Deploy Caddy":
            # These entries do not exist until enrollment. Caddy is also the
            # Seed-mode exception that must run before they can exist.
            additions.extend(BW_OPTIONAL)
        elif "lab-run" in scripts and name not in {"Vaultwarden Enrollment", "Vaultwarden Recovery"}:
            additions.extend(BW_OPTIONS)
        if name in {"Vaultwarden Enrollment", "Vaultwarden Cutover"}:
            additions.append(ADMIN_OPTION)
        if name in {"Reimport Jobs", "Vaultwarden Cutover"}:
            additions.append(RUNDECK_OPTION)
        if name in {"Deploy Caddy", "Vaultwarden Cutover"}:
            additions.append(CLOUDFLARE_OPTION)
        existing = {option.get("name") for option in job.get("options", [])}
        job.setdefault("options", []).extend(
            option for option in additions if option["name"] not in existing
        )
        if not job["options"]:
            job.pop("options", None)

    yaml.safe_dump(jobs, sys.stdout, sort_keys=False, width=1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
