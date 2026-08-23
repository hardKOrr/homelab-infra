#!/usr/bin/env python3
"""Publish, per application, the list of instances this lab actually has.

Each application Rundeck job carries an `instance` option whose value list is fetched from
`<outdir>/<slug>.json`. That is what makes a second instance a dropdown choice rather than
something the operator has to remember and retype: Deploy Radarr, Restart Radarr and Remove
Radarr all offer the same configured instances.

WHY A FILE AND NOT A JOB OPTION BAKED AT IMPORT TIME. Instances come and go between job
imports. A list rendered into the job definition would be correct only until the next
Configure job created one, and stale lists are worse than no list. `lab-run.sh` rewrites
these files before and after every job, so Configure publishes a new instance before it
exits.

WHICH APP AN INSTANCE BELONGS TO is read from the instance file's own name. The filename is
the only link between a config file and the application it configures. The longest matching
slug wins, so `uptime-kuma` is not mistaken for an instance of some app called `uptime`. An
instance file that matches no application is reported on stderr and appears in no list.

MULTI-ESTATE IDENTITY. Applications whose catalog scope is `estate` default to
`<app>-<default-estate>` once infrastructure.yml declares two or more estates. Every such
instance is named `<app>-<estate>[-<variant>]`; there is no unnamed primary estate.

Usage:
    app-instances.py --repo /path/to/checkout --out /var/lib/rundeck/app-instances
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml


def load_applications(catalog: Path) -> dict[str, dict]:
    with catalog.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    applications = document.get("applications") or {}
    if not isinstance(applications, dict):
        raise ValueError(f"{catalog}: applications must be a mapping")
    return {slug: value if isinstance(value, dict) else {} for slug, value in applications.items()}


def load_estates(infrastructure: Path) -> dict:
    if not infrastructure.is_file():
        return {"names": [], "default": "", "multiple": False}
    with infrastructure.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    domains = document.get("domains") or {}
    if not isinstance(domains, dict):
        raise ValueError(f"{infrastructure}: domains must be a mapping")
    names = list(domains)
    invalid = [
        name for name in names
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name)
    ]
    if invalid:
        raise ValueError(
            f"{infrastructure}: estate names must use lowercase letters, digits and hyphens"
        )
    if len(names) < 2:
        return {"names": names, "default": names[0] if names else "", "multiple": False}
    defaults = [
        name for name, value in domains.items()
        if isinstance(value, dict) and value.get("default") is True
    ]
    if len(defaults) != 1:
        raise ValueError(
            f"{infrastructure}: a multi-estate domains map must declare exactly one default: true"
        )
    return {"names": names, "default": defaults[0], "multiple": True}


def owning_slug(instance: str, slugs: list[str]) -> str | None:
    """The application an instance file belongs to, longest slug first."""
    candidates = [
        slug for slug in slugs
        if instance == slug or instance.startswith(f"{slug}-")
    ]
    return max(candidates, key=len) if candidates else None


def collect(repo: Path, applications: dict[str, dict], estates: dict) \
        -> tuple[dict[str, list], list[str], list[str]]:
    slugs = sorted(applications)
    apps_dir = repo / "config" / "apps"
    instances: dict[str, list[str]] = {}
    for slug in slugs:
        estate_scoped = applications[slug].get("scope", "lab") == "estate"
        default = (
            f"{slug}-{estates['default']}"
            if estate_scoped and estates["multiple"]
            else slug
        )
        instances[slug] = [default]
    unmatched: list[str] = []
    noncanonical: list[str] = []
    instance_estates: dict[str, str] = {}
    for path in sorted(apps_dir.glob("*.yml")) if apps_dir.is_dir() else []:
        instance = path.stem
        slug = owning_slug(instance, slugs)
        if slug is None:
            unmatched.append(instance)
            continue
        if instance not in instances[slug]:
            instances[slug].append(instance)
        if applications[slug].get("scope", "lab") == "estate" and estates["multiple"]:
            with path.open(encoding="utf-8") as handle:
                document = yaml.safe_load(handle) or {}
            routing = document.get("routing") if isinstance(document, dict) else {}
            selected = (
                routing.get("estate") if isinstance(routing, dict) else ""
            ) or estates["default"]
            instance_estates[instance] = selected
            if selected not in estates["names"]:
                noncanonical.append(
                    f"config/apps/{instance}.yml names undeclared estate {selected}"
                )
                continue
            prefix = f"{slug}-{selected}"
            if instance != prefix and not instance.startswith(f"{prefix}-"):
                noncanonical.append(
                    f"config/apps/{instance}.yml belongs to estate {selected}; new names use "
                    f"{prefix}[-<variant>]"
                )

    rendered: dict[str, list] = {}
    for slug, names in instances.items():
        ordered = sorted(names)
        if applications[slug].get("scope", "lab") == "estate" and estates["multiple"]:
            rendered[slug] = [
                {
                    "name": f"{name} — {instance_estates.get(name, estates['default'])}",
                    "value": name,
                }
                for name in ordered
            ]
        else:
            rendered[slug] = ordered
    return rendered, unmatched, noncanonical


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="repository checkout root")
    parser.add_argument("--out", required=True, type=Path, help="directory to write <slug>.json into")
    args = parser.parse_args()

    catalog = args.repo / "catalog" / "applications.yml"
    if not catalog.is_file():
        print(f"app-instances: no catalog at {catalog}", file=sys.stderr)
        return 1

    applications = load_applications(catalog)
    estates = load_estates(args.repo / "config" / "infrastructure.yml")
    instances, unmatched, noncanonical = collect(args.repo, applications, estates)

    args.out.mkdir(parents=True, exist_ok=True)
    for slug, names in instances.items():
        target = args.out / f"{slug}.json"
        payload = json.dumps(names, indent=2) + "\n"
        # Rewrite only on a real change: this runs before and after every job, and an
        # untouched mtime is the cheap signal that nothing about the lab moved.
        if not target.is_file() or target.read_text(encoding="utf-8") != payload:
            target.write_text(payload, encoding="utf-8")

    estate_target = args.out / "estates.json"
    estate_names = (
        [estates["default"]]
        + [name for name in estates["names"] if name != estates["default"]]
        if estates["names"] else []
    )
    estate_payload = json.dumps(estate_names, indent=2) + "\n"
    if not estate_target.is_file() or estate_target.read_text(encoding="utf-8") != estate_payload:
        estate_target.write_text(estate_payload, encoding="utf-8")

    if unmatched:
        print(
            "app-instances: no application matches "
            + ", ".join(f"config/apps/{name}.yml" for name in unmatched)
            + " - name an instance file <app>[-<variant>] in a single-estate lab or "
              "<app>-<estate>[-<variant>] in a multi-estate lab for it to appear in that "
              "application's instance list",
            file=sys.stderr,
        )
    if noncanonical:
        print("app-instances: invalid multi-estate instance name(s): "
              + "; ".join(noncanonical), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
