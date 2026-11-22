#!/usr/bin/env python3
"""Render classified Rundeck jobs with the secure options their execution path needs.

Two kinds of source file live in rundeck/jobs/:

  a JOB      — one file, one job. Its group is projected from catalog/applications.yml
               (the Deploy job of an app with its own `job:`) or from
               rundeck/job-groups.yml (a lab-wide operator job).

  a TEMPLATE — one file, one day-2 ACTION, expanded into one job per application the
               action applies to. rundeck/app-actions.yml says which action a template is
               and which facts it requires. The expansion answers the instance, app name
               and stack tag from the catalog and from the app's own defaults, so the
               operator is not asked for what the platform already knows.

deploy-app.yaml is the DEPLOY TEMPLATE: one Deploy job per catalog application without a
`job:` of its own, with that entry's `deploy_uuid` and a description built from its
`summary:` and its defaults.

Both are rendered by the same entry point and imported by the same loop: a template
renders as a multi-job YAML document, which the Rundeck import endpoint accepts exactly
like a single-job one.
"""

from __future__ import annotations

import copy
import re
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
RETIRED_JOBS = REPO_ROOT / "rundeck" / "retired-jobs.yml"
APP_DEFAULTS = REPO_ROOT / "ansible" / "vars" / "app-defaults"
INFRASTRUCTURE_CONFIG = REPO_ROOT / "config" / "infrastructure.yml"

# Placeholders a template must carry, so a template can never be mistaken for a job and
# imported as itself.
GROUP_TOKEN = "%GROUP%"
UUID_TOKEN = "%UUID%"

# Stable per-job identity. Rundeck imports with uuidOption=preserve, so an expanded job
# keeps its execution history across reimports only if this value never moves.
UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/hardKOrr/homelab-infra")

DEPLOY_TEMPLATE = "deploy-app.yaml"

ROOTS = ("Applications", "Platform")
HOSTING_KINDS = ("native", "docker", "kubernetes", "appliance")
# Facts an action may require; rundeck/app-actions.yml defines each one.
FACTS = ("recovery", "docker", "guest", "removable")


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
    secure_option(
        "bw_clientid",
        f"{BASE}/vaultwarden-machine/client-id",
        "**Automatic:** Vaultwarden automation API client ID loaded from encrypted Key Storage.",
    ),
    secure_option(
        "bw_clientsecret",
        f"{BASE}/vaultwarden-machine/client-secret",
        "**Automatic:** Vaultwarden automation API secret loaded from encrypted Key Storage.",
    ),
    secure_option(
        "bw_password",
        f"{BASE}/vaultwarden-machine/master-password",
        "**Automatic:** Vaultwarden automation master password loaded from encrypted Key Storage.",
    ),
]
BW_OPTIONAL = [option | {"required": False} for option in BW_OPTIONS]
ADMIN_OPTION = secure_option(
    "vaultwarden_admin_token",
    f"{BASE}/vaultwarden-machine/admin-token",
    "**Automatic:** Vaultwarden server admin token loaded from encrypted Key Storage.",
)
RUNDECK_OPTION = secure_option(
    "rundeck_api_token",
    f"{BASE}/rundeck/api-token",
    "**Automatic:** Rundeck control-plane API token loaded from encrypted Key Storage.",
)
CLOUDFLARE_OPTION = secure_option(
    "cloudflare_api_token",
    f"{BASE}/bootstrap/cloudflare-api-token",
    "**Automatic when present:** Cloudflare token with Zone Read and DNS Edit for the lab zone.",
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


def app_defaults_of(slug: str) -> dict:
    """The `<app>_defaults` dict for one application (CONTRACT.md §2)."""
    path = APP_DEFAULTS / f"{slug}.yml"
    if not path.exists():
        raise ValueError(
            f"{path}: an application in the catalog must have a defaults file — its hosting"
            " kind decides which day-2 jobs exist"
        )
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    return next(
        (value for key, value in document.items()
         if key.endswith("_defaults") and isinstance(value, dict)),
        {},
    )


def hosting_of(defaults: dict, slug: str) -> tuple[str, str]:
    """Day-2 action family and stack tag for an application, read from its defaults.

    `hosting.method` is the deployment contract. ISO-installed appliances remain a
    separate action family because they do not have the SSH and guest-agent interface
    used by ordinary native guests.
    """
    stack = defaults.get("stack") or ""
    declaration = defaults.get("hosting") or {}
    method = declaration.get("method") if isinstance(declaration, dict) else declaration
    method = method or ("docker" if stack else "native")
    if method not in {"native", "docker", "kubernetes"}:
        raise ValueError(
            f"{APP_DEFAULTS / (slug + '.yml')}: hosting.method {method!r} is not one of "
            "('native', 'docker', 'kubernetes')"
        )
    proxmox = defaults.get("proxmox") or {}
    install = defaults.get("install") or {}
    is_appliance = bool(proxmox.get("installer")) or install.get("method") == "official-iso"
    action_family = "appliance" if method == "native" and is_appliance else method
    if action_family not in HOSTING_KINDS:
        raise ValueError(f"{APP_DEFAULTS / (slug + '.yml')}: unsupported action family {action_family!r}")
    return action_family, str(stack)


def estate_context() -> dict:
    """Return the authored estate names and the one unambiguous default.

    A single-estate checkout keeps the historical `<app>` default. Once `domains:` has
    two or more entries, declaration order is not identity: exactly one entry must say
    `default: true`, and estate-scoped applications default to `<app>-<estate>`.
    """
    if not INFRASTRUCTURE_CONFIG.is_file():
        return {"names": [], "default": "", "multiple": False}
    with INFRASTRUCTURE_CONFIG.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    domains = document.get("domains") or {}
    if not isinstance(domains, dict):
        raise ValueError(f"{INFRASTRUCTURE_CONFIG}: domains must be a mapping")
    names = list(domains)
    invalid = [
        name for name in names
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name)
    ]
    if invalid:
        raise ValueError(
            f"{INFRASTRUCTURE_CONFIG}: estate names must use lowercase letters, digits and hyphens"
        )
    if len(names) < 2:
        return {
            "names": names,
            "default": names[0] if names else "",
            "multiple": False,
        }
    defaults = [
        name for name, value in domains.items()
        if isinstance(value, dict) and value.get("default") is True
    ]
    if len(defaults) != 1:
        raise ValueError(
            f"{INFRASTRUCTURE_CONFIG}: a multi-estate domains map must declare exactly one"
            " default: true"
        )
    return {"names": names, "default": defaults[0], "multiple": True}


def default_instance(app: dict, estates: dict) -> str:
    """The one-click default, with explicit identity in a multi-estate lab."""
    slug = app["slug"]
    if app["scope"] != "estate" or not estates["multiple"]:
        return slug
    return f"{slug}-{estates['default']}"


def set_application_options(job: dict, app: dict, estates: dict) -> None:
    """Attach the live instance provider and estate-aware defaults to one app job."""
    options = job.get("options") or []
    instance_option = next((option for option in options if option.get("name") == "instance"), None)
    if instance_option is not None:
        instance_option["value"] = default_instance(app, estates)
        instance_option["valuesUrl"] = (
            f"file:/var/lib/rundeck/app-instances/{app['slug']}.json"
        )
        instance_option["enforced"] = False
        if app["scope"] == "estate" and estates["multiple"]:
            instance_option["description"] = (
                str(instance_option.get("description") or "").rstrip()
                + f"\n\n**Multi-estate naming:** `{app['slug']}-<estate>[-<variant>]`."
                  " The dropdown label also identifies the estate."
            )

    # Configure is the audited path that creates the instance file. In a multi-estate
    # lab, make the estate explicit there so a new `<app>-<estate>` name and its authored
    # routing.estate cannot silently disagree.
    if job.get("name") == f"Configure {app['name']}":
        if app["scope"] == "lab":
            job["options"] = [option for option in options if option.get("name") != "estate"]
        elif estates["multiple"]:
            estate_option = next(
                (option for option in options if option.get("name") == "estate"), None
            )
            if estate_option is not None:
                estate_option["required"] = True
                estate_option["value"] = estates["default"]
                estate_option["valuesUrl"] = "file:/var/lib/rundeck/app-instances/estates.json"
                estate_option["enforced"] = True


def load_applications() -> dict[str, dict]:
    applications = load_document(APPLICATION_CATALOG, "applications", version=2)
    resolved: dict[str, dict] = {}
    seen_jobs: dict[str, str] = {}

    for slug, entry in applications.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{APPLICATION_CATALOG}: applications.{slug} must be a mapping")
        missing = [f for f in ("name", "root", "category") if not entry.get(f)]
        if missing:
            raise ValueError(
                f"{APPLICATION_CATALOG}: applications.{slug} is missing {', '.join(missing)}"
            )
        root, category = entry["root"], entry["category"]
        app_type = entry.get("type") or ""
        name, job = entry["name"], entry.get("job")
        # An app either keeps its own Deploy job file or takes the Deploy template, which
        # needs the job's identity and one line of operator text.
        templated = [field for field in ("summary", "deploy_uuid") if entry.get(field)]
        if bool(job) == bool(templated) or (templated and len(templated) != 2):
            raise ValueError(
                f"{APPLICATION_CATALOG}: applications.{slug} needs either job: (its own Deploy"
                " file) or both summary: and deploy_uuid: (the Deploy template)"
            )
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
        if job:
            seen_jobs[job] = slug

        defaults = app_defaults_of(slug)
        hosting, stack = hosting_of(defaults, slug)
        segments = [root, category] + ([app_type] if app_type else []) + [name]
        # `scope` is REQUIRED, not defaulted. Omitting it would put an application on the
        # shared side of the estate boundary silently, and the shared side is meant to be
        # the deliberate exception — a small named set of services the estates agree to
        # hold in common, not whatever nobody classified.
        scope = entry.get("scope")
        if scope not in {"lab", "estate"}:
            raise ValueError(
                f"{APPLICATION_CATALOG}: applications.{slug}.scope must be declared as"
                " lab (one deployment serves every estate) or estate (one deployment"
                " per estate)"
            )
        # `app-defaults/` is git-managed and ships to every lab; estate names are one
        # lab's. A default naming an estate is a name the next clone has never declared,
        # and its config-doctor rejects the instance that inherits it. The estate belongs
        # in config/apps/<instance>.yml, which is that lab's own statement.
        routing = defaults.get("routing") or {}
        if routing.get("estate"):
            raise ValueError(
                f"{APP_DEFAULTS / (slug + '.yml')}: routing.estate must not be declared in"
                " app defaults — an estate name belongs to one lab, and this file ships to"
                " every lab. Author it in config/apps/<instance>.yml."
            )
        resolved[slug] = {
            "slug": slug,
            "name": name,
            "job": job,
            "group": "/".join(segments),
            "hosting": hosting,
            "stack": stack,
            "essential": bool(entry.get("essential")),
            "extra": list(entry.get("extra") or []),
            "exclude": list(entry.get("exclude") or []),
            "scope": scope,
            "summary": str(entry.get("summary") or ""),
            "deploy_uuid": str(entry.get("deploy_uuid") or ""),
            "defaults": defaults,
            "facts": {
                "recovery": bool((defaults.get("recovery") or {}).get("methods")),
                "docker": any(
                    str(form).startswith("docker-")
                    for form in (defaults.get("hosting") or {}).get("supported") or []
                ),
                "guest": hosting != "appliance",
            },
        }
        if "actions" in entry:
            raise ValueError(
                f"{APPLICATION_CATALOG}: applications.{slug}.actions is retired — the"
                " action set is derived from facts (rundeck/app-actions.yml); use exclude:"
                " with a reason, or extra: for an app-specific action"
            )
    return resolved


def load_actions() -> dict[str, dict]:
    """Standard verbs (`actions`) and app-specific ones (`app_actions`), by name."""
    resolved: dict[str, dict] = {}
    for section, standard in (("actions", True), ("app_actions", False)):
        for action, entry in load_document(APP_ACTIONS, section, version=2).items():
            if not isinstance(entry, dict) or not entry.get("template"):
                raise ValueError(f"{APP_ACTIONS}: {section}.{action} needs a template")
            if action in resolved:
                raise ValueError(f"{APP_ACTIONS}: {action} is declared twice")
            requires = list(entry.get("requires") or [])
            unknown = [fact for fact in requires if fact not in FACTS]
            if unknown or (requires and not standard):
                raise ValueError(
                    f"{APP_ACTIONS}: {section}.{action} requires unknown or misplaced"
                    f" facts {requires}"
                )
            replaces = entry.get("replaces")
            if replaces is not None and (standard or replaces != "remove"):
                raise ValueError(
                    f"{APP_ACTIONS}: only an app_actions entry may declare replaces: remove"
                )
            resolved[action] = {
                "action": action,
                "template": entry["template"],
                "standard": standard,
                "requires": requires,
                "replaces": replaces,
            }
    return resolved


def actions_for(app: dict, actions: dict[str, dict]) -> list[str]:
    """Which day-2 actions this application gets, in a stable order."""
    named = app["extra"] + app["exclude"]
    unknown = [name for name in named if name not in actions]
    if unknown:
        raise ValueError(f"{APPLICATION_CATALOG}: {app['slug']} names unknown action(s) {unknown}")
    standard_extra = [name for name in app["extra"] if actions[name]["standard"]]
    if standard_extra:
        raise ValueError(
            f"{APPLICATION_CATALOG}: {app['slug']} extra: names standard action(s)"
            f" {standard_extra}; those are derived from facts"
        )
    replaced = {actions[name]["replaces"] for name in app["extra"]} - {None}
    facts = app["facts"] | {"removable": not app["essential"] and "remove" not in replaced}
    selected = [
        name
        for name, action in actions.items()
        if action["standard"] and all(facts[fact] for fact in action["requires"])
    ]
    selected += app["extra"]
    idle = [name for name in app["exclude"] if name not in selected]
    if idle:
        raise ValueError(
            f"{APPLICATION_CATALOG}: {app['slug']} excludes {idle}, which its facts already"
            " withhold; delete the exclude"
        )
    return sorted(name for name in selected if name not in app["exclude"])


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


def deploy_facts(app: dict) -> str:
    """The Deploy description's fact lines, read from the app's defaults."""
    defaults = app["defaults"]
    hosting = defaults.get("hosting") or {}
    method = hosting.get("method") or "native"
    where = {
        "docker": f"Docker on the `{app['stack']}` stack" if app["stack"] else "Docker on its own guest",
        "kubernetes": "Kubernetes (namespace `app-<instance>`)",
    }.get(method, "native on its own guest")
    others = [form for form in hosting.get("supported") or []
              if not form.startswith(method)]
    hosting_line = f"- **Hosting:** {where} by default"
    if others:
        hosting_line += (
            "; `hosting.method` in `config/apps/<instance>.yml` also allows "
            + ", ".join(f"`{form}`" for form in others)
        )
    routing = defaults.get("routing") or {}
    proxy = routing.get("proxy") or "none"
    route = (
        "none; it serves no web UI" if proxy == "none"
        else f"`{routing.get('subdomain') or '<instance>'}.<domain>`"
             + (", internal sources only" if proxy == "internal" else f", {proxy}")
    )
    recovery = (
        f"**Backup {app['name']}** and **Restore {app['name']}** under Maintenance"
        if app["facts"]["recovery"]
        else "rebuild-only; a Deploy recreates it"
    )
    role = (defaults.get("deploy") or {}).get("role") or app["slug"]
    return "\n".join([
        hosting_line + ".",
        f"- **Route:** {route}.",
        f"- **Recovery:** {recovery}.",
        f"- **Details:** `ansible/roles/{role}/`.",
    ])


def expand_deploy(template: list[dict], applications: dict[str, dict]) -> list[dict]:
    jobs: list[dict] = []
    estates = estate_context()
    for slug in sorted(applications):
        app = applications[slug]
        if app["job"]:
            continue
        job = substitute(copy.deepcopy(template[0]), {
            "%SLUG%": app["slug"],
            "%NAME%": app["name"],
            "%SUMMARY%": app["summary"],
            "%FACTS%": deploy_facts(app),
        })
        job["group"] = app["group"]
        job["id"] = app["deploy_uuid"]
        job["uuid"] = app["deploy_uuid"]
        set_application_options(job, app, estates)
        jobs.append(job)
    return jobs


def expand_template(template: list[dict], action: str, applications: dict[str, dict],
                    actions: dict[str, dict]) -> list[dict]:
    if action == "deploy":
        return expand_deploy(template, applications)
    jobs: list[dict] = []
    estates = estate_context()
    for slug in sorted(applications):
        app = applications[slug]
        if action not in actions_for(app, actions):
            continue
        job = substitute(copy.deepcopy(template[0]), {
            "%SLUG%": app["slug"],
            "%NAME%": app["name"],
            "%STACK%": app["stack"],
            "%SCOPE%": app["scope"],
        })
        job["group"] = f"{app['group']}/Maintenance"
        identity = str(uuid.uuid5(UUID_NAMESPACE, f"rundeck/app-action/{action}/{slug}"))
        job["id"] = identity
        job["uuid"] = identity
        set_application_options(job, app, estates)
        jobs.append(job)
    return jobs


def load_job(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        jobs = yaml.safe_load(handle)
    if not isinstance(jobs, list) or len(jobs) != 1 or not isinstance(jobs[0], dict):
        raise ValueError(f"{path}: Rundeck job document must contain exactly one job")
    return jobs


def load_retired_jobs() -> dict[str, str]:
    retired = load_document(RETIRED_JOBS, "jobs")
    for identity, name in retired.items():
        try:
            parsed = str(uuid.UUID(str(identity)))
        except ValueError as error:
            raise ValueError(f"{RETIRED_JOBS}: {identity!r} is not a UUID") from error
        if parsed != identity or not isinstance(name, str) or not name:
            raise ValueError(
                f"{RETIRED_JOBS}: retired job UUIDs and names must be canonical non-empty strings"
            )
    return retired


def classify() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return (single-job groups, deploy-job names, template file -> action)."""
    applications = load_applications()
    actions = load_actions()

    owned = [app for app in applications.values() if app["job"]]
    groups = {app["job"]: app["group"] for app in owned}
    names = {app["job"]: f"Deploy {app['name']}" for app in owned}

    templates: dict[str, str] = {DEPLOY_TEMPLATE: "deploy"}
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

    collisions = sorted(set(identities) & set(load_retired_jobs()))
    if collisions:
        raise ValueError(
            f"{RETIRED_JOBS}: active job UUID(s) are also retired: {', '.join(collisions)}"
        )


def render(job_path: Path) -> list[dict]:
    groups, _, templates = classify()
    jobs = load_job(job_path)
    applications = load_applications()

    if job_path.name in templates:
        jobs = expand_template(jobs, templates[job_path.name], applications, load_actions())
    else:
        if job_path.name not in groups:
            raise ValueError(f"{job_path.name}: job is not classified")
        # Group is a projection of the repository-owned classifications. The source value
        # remains for reviewability and --check rejects drift.
        jobs[0]["group"] = groups[job_path.name]
        app = next((item for item in applications.values() if item["job"] == job_path.name), None)
        if app is not None:
            set_application_options(jobs[0], app, estate_context())

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
        if name in {"Reimport Jobs", "Vaultwarden Enrollment", "Vaultwarden Cutover"}:
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

    # Expanded jobs share option dictionaries. Emit independent YAML values so
    # large catalogs remain within Rundeck/SnakeYAML's alias protection limit.
    class JobDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    yaml.dump(jobs, sys.stdout, Dumper=JobDumper, sort_keys=False, width=1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
