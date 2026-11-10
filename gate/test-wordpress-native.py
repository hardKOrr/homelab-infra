#!/usr/bin/env python3
"""Guard WordPress's native method: exact pins, the rendered config and site, the wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/wordpress"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/wordpress.yml").read_text())["wordpress_defaults"]

# One exact upstream release for both methods, verified against a pinned checksum.
app = defaults["app"]
assert app["native_version"] == "6.9.4" and app["image"].startswith(f"wordpress:{app['native_version']}-"), app
assert len(app["native_sha256"]) == 64 and len(app["native_cli_sha256"]) == 64
assert app["cli_image"].startswith(f"wordpress:cli-{app['native_cli_version']}-"), app
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native.yml").read_text()
assert "https://wordpress.org/wordpress-{{ app_config.app.native_version }}.tar.gz" in install
assert "native_sha256" in install and "native_cli_sha256" in install

playbook = (repo / "ansible/playbooks/apps/wordpress.yml").read_text()
assert "tasks_from: provision" in playbook and "in ['docker', 'native']" in playbook
plays = yaml.safe_load(playbook)
deploy = next(play for play in plays if play["name"] == "WordPress | Deploy")
for task in deploy["tasks"]:
    if task["name"] in ("Bootstrap guest", "Deploy WordPress"):
        assert "in ['docker', 'native']" in str(task["when"]), task["name"]

# Docker's Compose-only pieces stay out of the native path, and every WP-CLI call goes
# through one prefix that the method selects.
main = (role / "tasks/main.yml").read_text()
assert "docker compose" not in main and "- docker\n" not in main, "WP-CLI calls use wordpress_cli_argv"
assert "runuser" in (role / "defaults/main.yml").read_text()

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    variables = {"instance": "wordpress", "wordpress_native_root": "/var/www/wordpress",
                 "wordpress_native_keys": "/opt/wordpress/keys.php",
                 "homelabinfra_infra": {"reverse_proxy": {"host": "https://proxy.example.invalid"}},
                 "app_config": {"app": {**app, "database_host": "db.example.invalid", "database_port": 3306,
                                        "database_user": "wp", "database_password": "pa'ss\\word"}}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / f"templates/{name}.j2"), "dest": str(root / name)}}
        for name in ("wp-config.php", "wordpress-apache.conf")]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    config = (root / "wp-config.php").read_text()
    site = (root / "wordpress-apache.conf").read_text()
assert "define( 'DB_PASSWORD', 'pa\\'ss\\\\word' );" in config, config
assert "define( 'DB_HOST', 'db.example.invalid:3306' );" in config
assert "require_once '/opt/wordpress/keys.php';" in config and "wp-settings.php" in config
assert f"<VirtualHost *:{app['port']}>" in site and "DocumentRoot /var/www/wordpress" in site
assert "REMOTE_ADDR} == 'proxy.example.invalid'" in site

# Backup and restore go through the shared operations with the method's runtime.
backup = (role / "tasks/backup.yml").read_text()
assert "tasks/backup/pbs-client-backup.yml" in backup and "pbs_client_runtime: native" in backup
assert "database.pxar" in backup and "content.pxar" in backup
assert "tasks/database/dump.yml" in (role / "tasks/backup-quiesced.yml").read_text()
restore = (role / "tasks/restore.yml").read_text()
assert "wordpress_native | bool" in restore and "tasks/recovery/restore-swap.yml" in restore

print("wordpress native pins one release, renders its config and site and uses the shared backup and restore")
