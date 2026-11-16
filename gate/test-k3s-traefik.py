#!/usr/bin/env python3
"""Traefik keeps the forwarded scheme Caddy sends from the lab subnets and the pod network.

Without trust Traefik replaces X-Forwarded-Proto: https with http and WordPress's login
redirects to itself. Renders the k3s role's defaults and Traefik template in a real play.
"""
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/k3s_cluster"
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"

with tempfile.TemporaryDirectory() as directory:
    out = Path(directory) / "traefik.yaml"
    play = Path(directory) / "render.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False,
        "vars_files": [str(ROLE / "defaults/main.yml")],
        "vars": {
            "k8s_cluster_config": {"ingress": {"vip": "192.0.2.10"}},
            "homelabinfra_config": {"networks": {
                "default": {"cidr": "192.0.2.0/24"},
                "iot": {"cidr": "198.51.100.0/25"},
                "dhcp-only": {"cidr": "dhcp"},
            }},
        },
        "tasks": [{"ansible.builtin.template": {
            "src": str(ROLE / "templates/traefik-config.yaml.j2"), "dest": str(out)}}],
    }]))
    result = subprocess.run([str(ANSIBLE), "-i", "localhost,", "-c", "local", str(play)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    config = yaml.safe_load(out.read_text())

values = yaml.safe_load(config["spec"]["valuesContent"])
web = values["ports"]["web"]
assert web["forwardedHeaders"]["trustedIPs"] == ["192.0.2.0/24", "198.51.100.0/25", "10.42.0.0/16"], web
assert "insecure" not in web["forwardedHeaders"], "trust named ranges, never every source"
assert values["service"]["annotations"]["metallb.io/loadBalancerIPs"] == "192.0.2.10"
print("k3s Traefik: forwarded headers trusted from the lab subnets and the pod network")
