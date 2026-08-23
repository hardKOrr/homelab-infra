#!/usr/bin/env python3
"""Publish, per application, the list of instances this lab actually has.

Each generated Rundeck job carries an `instance` option whose value list is fetched from
`<outdir>/<slug>.json`. That is what makes a second instance a dropdown choice rather than
something the operator has to remember and retype: Deploy Radarr, Restart Radarr and Remove
Radarr all offer `radarr` and `radarr-4k` once the second instance file exists.

WHY A FILE AND NOT A JOB OPTION BAKED AT IMPORT TIME. Instances come and go between job
imports. A list rendered into the job definition would be correct only until the next
Configure job created one, and stale lists are worse than no list. `lab-run.sh` rewrites
these files at the start of every job, so the list is never older than the last thing the
lab did.

WHICH APP AN INSTANCE BELONGS TO is read from the instance file's own name. An instance
file is named `<app>` or `<app>-<suffix>` — that is already the documented convention in
every config.example/apps/*.example.yml header ("radarr-4k.yml, radarr-hd.yml"), and it is
the only link between a config file and the application it configures; nothing else in the
tree records it. The longest matching slug wins, so `uptime-kuma` is not mistaken for an
instance of some app called `uptime`. An instance file that matches no application is
reported on stderr and simply appears in no list; the option is not `enforced`, so such an
instance can still be typed in by hand.

Usage:
    app-instances.py --repo /path/to/checkout --out /var/lib/rundeck/app-instances
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


def load_slugs(catalog: Path) -> list[str]:
    with catalog.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    applications = document.get("applications") or {}
    if not isinstance(applications, dict):
        raise ValueError(f"{catalog}: applications must be a mapping")
    return sorted(applications)


def owning_slug(instance: str, slugs: list[str]) -> str | None:
    """The application an instance file belongs to, longest slug first."""
    candidates = [
        slug for slug in slugs
        if instance == slug or instance.startswith(f"{slug}-")
    ]
    return max(candidates, key=len) if candidates else None


def collect(repo: Path, slugs: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    instances: dict[str, list[str]] = {slug: [slug] for slug in slugs}
    unmatched: list[str] = []
    apps_dir = repo / "config" / "apps"
    for path in sorted(apps_dir.glob("*.yml")) if apps_dir.is_dir() else []:
        instance = path.stem
        slug = owning_slug(instance, slugs)
        if slug is None:
            unmatched.append(instance)
            continue
        if instance not in instances[slug]:
            instances[slug].append(instance)
    return {slug: sorted(names) for slug, names in instances.items()}, unmatched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="repository checkout root")
    parser.add_argument("--out", required=True, type=Path, help="directory to write <slug>.json into")
    args = parser.parse_args()

    catalog = args.repo / "catalog" / "applications.yml"
    if not catalog.is_file():
        print(f"app-instances: no catalog at {catalog}", file=sys.stderr)
        return 1

    slugs = load_slugs(catalog)
    instances, unmatched = collect(args.repo, slugs)

    args.out.mkdir(parents=True, exist_ok=True)
    for slug, names in instances.items():
        target = args.out / f"{slug}.json"
        payload = json.dumps(names, indent=2) + "\n"
        # Rewrite only on a real change: this runs at the start of every job, and an
        # untouched mtime is the cheap signal that nothing about the lab moved.
        if not target.is_file() or target.read_text(encoding="utf-8") != payload:
            target.write_text(payload, encoding="utf-8")

    if unmatched:
        print(
            "app-instances: no application matches "
            + ", ".join(f"config/apps/{name}.yml" for name in unmatched)
            + " - name an instance file <app> or <app>-<suffix> for it to appear in that "
              "application's instance list",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
