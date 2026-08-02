#!/usr/bin/env python3
"""Render a Rundeck job with the secure options its execution path needs."""

from __future__ import annotations

import sys

import yaml


PROJECT = "homelab-infra"
BASE = f"keys/project/{PROJECT}"


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


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: render-job.py JOB.yaml", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as handle:
        jobs = yaml.safe_load(handle)
    if not isinstance(jobs, list):
        raise SystemExit("Rundeck job document must be a list")

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

    yaml.safe_dump(jobs, sys.stdout, sort_keys=False, width=1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
