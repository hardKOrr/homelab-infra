#!/usr/bin/env python3
"""Render classified Rundeck jobs with the secure options their execution path needs.

Two kinds of source file live in rundeck/jobs/:

  a JOB      — one file, one job. Its group is projected from catalog/applications.yml
               (a Deploy job) or from rundeck/job-groups.yml (a lab-wide operator job).

  a TEMPLATE — one file, one day-2 ACTION, expanded into one job per application the
               action applies to. rundeck/app-actions.yml says which action a template is
               and which hosting kinds implement it. The expansion answers the instance,
               app name and stack tag from the catalog and from the app's own defaults, so
               the operator is not asked for what the platform already knows.

Both are rendered by the same entry point and imported by the same loop: a template
renders as a multi-job YAML document, which the Rundeck import endpoint accepts exactly
like a single-job one.
"""

from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path

import yaml


PROJECT = "homelab-infra"
BASE = f"keys/project/{PROJECT}"
REPO_ROOT = Path(__file__).resolve().parent.parent
APPLICATION_CATALOG = REPO_ROOT / "catalog" / "applications.yml"
JOB_GROUPS = REPO_ROOT / "rundeck" / "job-groups.yml"
APP_ACTIONS = REPO_ROOT / "rundeck" / "app-actions.yml"
APP_DEFAULTS = REPO_ROOT / "ansible" / "vars" / "app-defaults"

# Placeholders a template must carry, so a template can never be mistaken for a job and
# imported as itself.
GROUP_TOKEN = "%GROUP%"
UUID_TOKEN = "%UUID%"

# Stable per-job identity. Rundeck imports with uuidOption=preserve, so an expanded job
# keeps its execution history across reimports only if this value never moves.
UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/hardKOrr/homelab-infra")

ROOTS = ("Applications", "Platform")
HOSTING_KINDS = ("native", "docker", "kubernetes")


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


def load_document(path: Path, key: str, version: int = 1) -> dict:
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict) or document.get("schema_version") != version:
        raise ValueError(f"{path}: expected schema_version: {version}")
    mapping = document.get(key)
    if not isinstance(mapping, dict):
        raise ValueError(f"{path}: {key} must be a mapping")
    return mapping


def hosting_of(slug: str) -> tuple[str, str]:
    """Hosting kind and stack tag for an application, read from its own defaults.

    This is the platform's existing rule, not a second one: an explicit `hosting:` wins,
    otherwise the presence of `stack:` is what tells a Docker app from a native LXC app.
    """
    path = APP_DEFAULTS / f"{slug}.yml"
    if not path.exists():
        raise ValueError(
            f"{path}: an application in the catalog must have a defaults file — its hosting"
            " kind decides which day-2 jobs exist"
        )
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    # Each defaults file wraps its content in one `<app>_defaults:` key (CONTRACT.md §2).
    defaults = next(
        (value for key, value in document.items()
         if key.endswith("_defaults") and isinstance(value, dict)),
        {},
    )
    stack = defaults.get("stack") or ""
    hosting = defaults.get("hosting") or ("docker" if stack else "native")
    if hosting not in HOSTING_KINDS:
        raise ValueError(f"{path}: hosting {hosting!r} is not one of {HOSTING_KINDS}")
    return hosting, str(stack)


def load_applications() -> dict[str, dict]:
    applications = load_document(APPLICATION_CATALOG, "applications", version=2)
    resolved: dict[str, dict] = {}
    seen_jobs: dict[str, str] = {}

    for slug, entry in applications.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{APPLICATION_CATALOG}: applications.{slug} must be a mapping")
        missing = [f for f in ("name", "job", "root", "category") if not entry.get(f)]
        if missing:
            raise ValueError(
                f"{APPLICATION_CATALOG}: applications.{slug} is missing {', '.join(missing)}"
            )
        root, category = entry["root"], entry["category"]
        app_type = entry.get("type") or ""
        name, job = entry["name"], entry["job"]
        if root not in ROOTS:
            raise ValueError(
                f"{APPLICATION_CATALOG}: applications.{slug}.root must be one of {ROOTS}"
            )
        for field, value in (("category", category), ("type", app_type), ("name", name)):
            if not isinstance(value, str) or "/" in value:
                raise ValueError(
                    f"{APPLICATION_CATALOG}: applications.{slug}.{field} must be one group segment"
                )
        if job in seen_jobs:
            raise ValueError(
                f"{APPLICATION_CATALOG}: {job} is claimed by {seen_jobs[job]} and {slug}"
            )
        seen_jobs[job] = slug

        hosting, stack = hosting_of(slug)
        segments = [root, category] + ([app_type] if app_type else []) + [name]
        resolved[slug] = {
            "slug": slug,
            "name": name,
            "job": job,
            "group": "/".join(segments),
            "hosting": hosting,
            "stack": stack,
            "essential": bool(entry.get("essential")),
            "extra": list(entry.get("extra") or []),
            "actions": entry.get("actions"),
        }
    return resolved


def load_actions() -> dict[str, dict]:
    actions = load_document(APP_ACTIONS, "actions")
    resolved: dict[str, dict] = {}
    for action, entry in actions.items():
        if not isinstance(entry, dict) or not entry.get("template"):
            raise ValueError(f"{APP_ACTIONS}: actions.{action} needs a template")
        hosting = entry.get("hosting") or []
        unknown = [kind for kind in hosting if kind not in HOSTING_KINDS]
        if unknown:
            raise ValueError(f"{APP_ACTIONS}: actions.{action} names unknown hosting {unknown}")
        resolved[action] = {
            "action": action,
            "template": entry["template"],
            "hosting": list(hosting),
            "essential_excluded": bool(entry.get("essential_excluded")),
            "opt_in": bool(entry.get("opt_in")),
        }
    return resolved


def actions_for(app: dict, actions: dict[str, dict]) -> list[str]:
    """Which day-2 actions this application gets, in a stable order."""
    if app["actions"] is not None:
        selected = list(app["actions"])
    else:
        selected = [
            name
            for name, action in actions.items()
            if not action["opt_in"]
            and app["hosting"] in action["hosting"]
            and not (action["essential_excluded"] and app["essential"])
        ]
        selected += [name for name in app["extra"] if name not in selected]
    unknown = [name for name in selected if name not in actions]
    if unknown:
        raise ValueError(f"{APPLICATION_CATALOG}: {app['slug']} names unknown action(s) {unknown}")
    return sorted(selected)


def substitute(node, values: dict[str, str]):
    """Replace %TOKEN% placeholders everywhere in a loaded job document."""
    if isinstance(node, dict):
        return {key: substitute(value, values) for key, value in node.items()}
    if isinstance(node, list):
        return [substitute(item, values) for item in node]
    if isinstance(node, str):
        for token, value in values.items():
            node = node.replace(token, value)
        return node
    return node


def expand_template(template: list[dict], action: str, applications: dict[str, dict],
                    actions: dict[str, dict]) -> list[dict]:
    jobs: list[dict] = []
    for slug in sorted(applications):
        app = applications[slug]
        if action not in actions_for(app, actions):
            continue
        job = substitute(copy.deepcopy(template[0]), {
            "%SLUG%": app["slug"],
            "%NAME%": app["name"],
            "%STACK%": app["stack"],
        })
        job["group"] = f"{app['group']}/Maintenance"
        identity = str(uuid.uuid5(UUID_NAMESPACE, f"rundeck/app-action/{action}/{slug}"))
        job["id"] = identity
        job["uuid"] = identity
        jobs.append(job)
    return jobs


def load_job(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        jobs = yaml.safe_load(handle)
    if not isinstance(jobs, list) or len(jobs) != 1 or not isinstance(jobs[0], dict):
        raise ValueError(f"{path}: Rundeck job document must contain exactly one job")
    return jobs


def classify() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return (single-job groups, deploy-job names, template file -> action)."""
    applications = load_applications()
    actions = load_actions()

    groups = {app["job"]: app["group"] for app in applications.values()}
    names = {app["job"]: f"Deploy {app['name']}" for app in applications.values()}

    templates: dict[str, str] = {}
    for action in actions.values():
        template = action["template"]
        if template in groups or template in templates:
            raise ValueError(f"{APP_ACTIONS}: {template} is classified more than once")
        templates[template] = action["action"]

    for job, group in load_document(JOB_GROUPS, "jobs").items():
        if not isinstance(job, str) or not isinstance(group, str) or not group:
            raise ValueError(f"{JOB_GROUPS}: job names and groups must be non-empty strings")
        if job in groups or job in templates:
            raise ValueError(f"{JOB_GROUPS}: {job} is also classified as an application job")
        groups[job] = group

    return groups, names, templates


def check_tree(job_directory: Path) -> None:
    groups, names, templates = classify()
    applications = load_applications()
    actions = load_actions()

    actual = {path.name for path in job_directory.glob("*.yaml")}
    expected = set(groups) | set(templates)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing jobs: {', '.join(missing)}")
        if unexpected:
            details.append(f"unclassified jobs: {', '.join(unexpected)}")
        raise ValueError(f"{job_directory}: {'; '.join(details)}")

    for filename in sorted(templates):
        job = load_job(job_directory / filename)[0]
        if job.get("group") != GROUP_TOKEN:
            raise ValueError(
                f"{filename}: a per-application template must declare group: '{GROUP_TOKEN}'"
                " — its real group is one per application"
            )
        if job.get("uuid") != UUID_TOKEN or job.get("id") != UUID_TOKEN:
            raise ValueError(
                f"{filename}: a per-application template must declare id and uuid as"
                f" '{UUID_TOKEN}' — each expansion gets its own stable identity"
            )
        if "%NAME%" not in str(job.get("name", "")):
            raise ValueError(f"{filename}: the job name must name the application with %NAME%")
        if not expand_template([job], templates[filename], applications, actions):
            raise ValueError(
                f"{filename}: no application selects the {templates[filename]} action"
            )

    for filename in sorted(groups):
        job = load_job(job_directory / filename)[0]
        if job.get("group") != groups[filename]:
            raise ValueError(
                f"{filename}: group is {job.get('group')!r}; expected {groups[filename]!r}"
            )
        if filename in names and job.get("name") != names[filename]:
            raise ValueError(
                f"{filename}: name is {job.get('name')!r}; expected {names[filename]!r}"
            )

    identities: dict[str, str] = {}
    for filename in sorted(expected):
        for job in render(job_directory / filename):
            identity = job.get("uuid")
            if identity in identities:
                raise ValueError(
                    f"{filename}: uuid {identity} collides with {identities[identity]}"
                )
            identities[identity] = f"{filename}:{job.get('name')}"


def render(job_path: Path) -> list[dict]:
    groups, _, templates = classify()
    jobs = load_job(job_path)

    if job_path.name in templates:
        jobs = expand_template(jobs, templates[job_path.name], load_applications(), load_actions())
    else:
        if job_path.name not in groups:
            raise ValueError(f"{job_path.name}: job is not classified")
        # Group is a projection of the repository-owned classifications. The source value
        # remains for reviewability and --check rejects drift.
        jobs[0]["group"] = groups[job_path.name]

    for job in jobs:
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

    return jobs


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
    try:
        jobs = render(Path(sys.argv[1]))
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise SystemExit(f"ERROR: {error}")

    yaml.safe_dump(jobs, sys.stdout, sort_keys=False, width=1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
