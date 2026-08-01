#!/usr/bin/env python3
"""Parse every Jinja expression in ansible/ and report the ones Jinja cannot compile.

Why this exists — blocker 16 of the live bring-up. tasks/proxmox/lxc-create.yml carried a
YAML `#` comment indented inside a `{{ ... }}` block:

    homelabinfra_instance: >-
      {{ homelabinfra_instance | combine({'lxc': dict(
          # 'started', not 'present': ...
          state = 'started',

Jinja has no `#` comment form, so that text is expression source and every LXC create died
with "unexpected char '#' at 53". Both existing gates were green: ansible-lint and
`ansible-playbook --syntax-check` parse the YAML and never compile the template string, so
a Jinja expression is only ever validated by running the task that renders it.

This walks every string scalar in every YAML file under ansible/ and calls
`jinja2.Environment().parse()` on the ones containing `{{` or `{%`. It catches syntax
errors only — undefined names, wrong filters and bad types still need a real run — but
syntax is the class that silently reaches production, because it costs a live provision to
discover.

Filters and tests supplied by Ansible and its collections are not registered here, which is
fine: an unknown filter NAME is still valid Jinja syntax and parses clean. Only genuinely
malformed expressions are reported.
"""

import os
import sys

import jinja2
import yaml

SCAN_ROOT = "ansible"
SKIP_DIRS = {".ansible", "__pycache__", ".git"}

env = jinja2.Environment()
findings = []


def walk(node, path, filename):
    if isinstance(node, str):
        if "{{" in node or "{%" in node:
            try:
                env.parse(node)
            except jinja2.TemplateSyntaxError as exc:
                findings.append((filename, path, str(exc), node))
    elif isinstance(node, dict):
        for key, value in node.items():
            walk(value, f"{path}.{key}", filename)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            walk(value, f"{path}[{index}]", filename)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else SCAN_ROOT
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith((".yml", ".yaml")):
                continue
            full = os.path.join(dirpath, filename)
            scanned += 1
            try:
                with open(full, encoding="utf-8") as handle:
                    documents = list(yaml.safe_load_all(handle))
            except yaml.YAMLError as exc:
                findings.append((full, "<document>", f"YAML parse error: {exc}", ""))
                continue
            for document in documents:
                walk(document, "", full)

    for filename, path, message, source in findings:
        print(f"{filename}: {path or '<root>'}: {message}")
        if source:
            excerpt = source.strip().splitlines()
            for line in excerpt[:8]:
                print(f"    {line}")
            if len(excerpt) > 8:
                print("    ...")
        print()

    print(f"jinja-parse: {scanned} file(s) scanned, {len(findings)} bad expression(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
