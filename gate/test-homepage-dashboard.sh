#!/bin/bash
# Verify Homepage builds entries only from approved topology fields and never renders the
# credential-shaped fields that can coexist in the in-memory registry.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
work="$(mktemp -d "${TMPDIR:-/tmp}/homepage-dashboard.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT

cat > "$work/play.yml" <<'YAML'
---
- hosts: localhost
  connection: local
  gather_facts: false
  vars:
    app_config:
      presentation:
        title: Test Lab
        theme: dark
        color: slate
        platform_group: Platform
        media_group: Media
        layout: {}
    homelabinfra_infra:
      vaultwarden: {host: "https://vault.example.test", admin_token: "must-not-render"}
      monitoring: {host: "https://status.example.test", password: "must-not-render"}
      media:
        sonarr: {app: sonarr, host: "http://sonarr.example.test:8989", api_key: "must-not-render"}
  tasks:
    - ansible.builtin.include_role:
        name: homepage
        tasks_from: dashboard
    - ansible.builtin.copy:
        content: "{{ homepage_service_groups | to_nice_yaml(indent=2, width=120) }}"
        dest: "{{ lookup('env', 'HOMEPAGE_TEST_OUTPUT') }}"
        mode: '0600'
YAML

HOMEPAGE_TEST_OUTPUT="$work/services.yml" ANSIBLE_ROLES_PATH="$repo/ansible/roles" \
  "$HOME/.venvs/homelab-ansible/bin/ansible-playbook" -i localhost, "$work/play.yml" >/dev/null

"$HOME/.venvs/homelab-ansible/bin/python" - "$work/services.yml" "$repo" <<'PYTHON'
from pathlib import Path
import sys
import yaml

services = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert "must-not-render" not in str(services), services
assert services == [
    {"Platform": [
        {"name": "Vaultwarden", "href": "https://vault.example.test", "description": "Secret store"},
        {"name": "Uptime Kuma", "href": "https://status.example.test", "description": "Uptime monitoring"},
    ]},
    {"Media": [
        {"name": "sonarr", "href": "http://sonarr.example.test:8989", "description": "sonarr application"},
    ]},
], services
repo = Path(sys.argv[2])
manifest = (repo / "ansible/roles/homepage/templates/manifest.yaml.j2").read_text(encoding="utf-8")
assert "PersistentVolumeClaim" not in manifest
assert "readOnlyRootFilesystem: true" in manifest
assert "automountServiceAccountToken: false" in manifest
catalog = yaml.safe_load((repo / "catalog/applications.yml").read_text(encoding="utf-8"))
assert catalog["applications"]["homepage"]["exclude"] == ["backup", "restore"]
print("homepage dashboard generation and redaction checks passed")
PYTHON
