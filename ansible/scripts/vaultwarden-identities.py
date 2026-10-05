#!/usr/bin/env python3
"""Read-only enrollment identity policy shared by bootstrap and Ansible."""
import argparse
import json
import os
import sys

import yaml


def resolve(config, environ):
    vault = config.get("vaultwarden") or {}
    identities = {
        "owner_email": vault.get("owner_email") or "",
        "automation_email": vault.get("automation_email") or
        "homelab-infra@" + (config.get("domain") or ""),
    }
    for field, declared in identities.items():
        variable = "VAULTWARDEN_" + field.upper()
        supplied = environ.get(variable) or ""
        if supplied and supplied != declared:
            raise ValueError(
                f"{variable} conflicts with config/infrastructure.yml vaultwarden.{field}. "
                "Preserved configuration is authoritative; unset the input or align it "
                "with the declared identity before retrying. No identity was changed."
            )
    return identities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", help="infrastructure YAML; otherwise read JSON on stdin")
    parser.add_argument("--lines", action="store_true", help="emit owner and automation on separate lines")
    args = parser.parse_args()
    if args.config:
        with open(args.config, encoding="utf-8") as source:
            config = yaml.safe_load(source) or {}
    else:
        config = json.load(sys.stdin)
    try:
        identities = resolve(config, os.environ)
    except ValueError as error:
        sys.exit(str(error))
    if args.lines:
        print(identities["owner_email"])
        print(identities["automation_email"])
    else:
        print(json.dumps(identities))


if __name__ == "__main__":
    main()
