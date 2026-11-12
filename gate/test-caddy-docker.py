#!/usr/bin/env python3
"""Caddy's Docker method renders a host-network Compose project, compiling in only the
modules the configuration needs, and the native default keeps its apt path."""
import sys
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "ansible/roles/caddy/templates"
env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), undefined=jinja2.StrictUndefined)
IMAGE = "caddy:2.10.2"


def render(name, modules):
    return env.get_template(name).render(
        instance="caddy", app_config={"app": {"image": IMAGE}}, caddy_project_dir="/opt/caddy",
        caddy_config_file="/opt/caddy/caddy.json", caddy_data_dir="/opt/caddy/data",
        caddy_config_dir="/opt/caddy/config", _caddy_modules_wanted=modules, _caddy_image_tag="abc123",
    )


plain = yaml.safe_load(render("docker-compose.yml.j2", []))["services"]["caddy"]
assert plain["image"] == IMAGE and "build" not in plain, plain
assert plain["network_mode"] == "host" and "--resume" in plain["command"], plain
assert "/opt/caddy/data:/data" in plain["volumes"] and "/opt/caddy/config:/config" in plain["volumes"]

custom = yaml.safe_load(render("docker-compose.yml.j2", ["github.com/caddy-dns/cloudflare"]))["services"]["caddy"]
assert custom["image"] == "homelab-caddy:abc123" and custom["build"]["context"] == "/opt/caddy/build", custom
dockerfile = render("Dockerfile.j2", ["github.com/caddy-dns/cloudflare", "github.com/example/two"])
assert f"FROM {IMAGE}-builder" in dockerfile and f"FROM {IMAGE}\n" in dockerfile
assert "--with github.com/caddy-dns/cloudflare" in dockerfile and "--with github.com/example/two" in dockerfile

defaults = yaml.safe_load((ROOT / "ansible/vars/app-defaults/caddy.yml").read_text())["caddy_defaults"]
assert defaults["hosting"]["method"] == "native"
assert defaults["hosting"]["supported"] == ["native-lxc", "docker-lxc", "docker-vm"]
print("Caddy Docker method renders as expected.")
sys.exit(0)
