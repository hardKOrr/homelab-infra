#!/usr/bin/env python3
"""Guard WordPress's native method: exact pins, the rendered config and site, the wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml
from jinja2 import Environment

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/wordpress"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/wordpress.yml").read_text())["wordpress_defaults"]

# One exact upstream release for both methods, verified against a pinned checksum.
app = defaults["app"]
assert app["native_version"] == "6.9.4" and app["image"].startswith(f"wordpress:{app['native_version']}-"), app
assert len(app["native_sha256"]) == 64 and len(app["native_cli_sha256"]) == 64
assert app["cli_image"].startswith(f"wordpress:cli-{app['native_cli_version']}-"), app
install = (role / "tasks/native.yml").read_text()
assert "https://wordpress.org/wordpress-{{ app_config.app.native_version }}.tar.gz" in install
assert "native_sha256" in install and "native_cli_sha256" in install

# Restore App's restore reaches the guest swap on Docker and native, the Job on Kubernetes.
dispatch = yaml.safe_load((role / "tasks/restore.yml").read_text())
for method in ("docker", "native", "kubernetes"):
    taken = [task["ansible.builtin.include_tasks"] for task in dispatch
             if Environment().compile_expression(task["when"])(app_config={"hosting": {"method": method}})]
    assert taken == (["restore-kubernetes.yml"] if method == "kubernetes" else ["restore-guest.yml"]), method

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
    # A registry from before source_addresses existed: the shared task falls back to host.
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/wiring/proxy-sources.yml")}] + [
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
assert "REMOTE_ADDR} in { 'proxy.example.invalid' }" in site

# Backup and restore go through the shared operations with the method's runtime.
backup = (role / "tasks/backup-native.yml").read_text()
assert "pbs_client_runtime: native" in backup
assert "database.pxar" in backup and "content.pxar" in backup
assert "tasks/database/dump.yml" in (role / "tasks/backup-quiesced.yml").read_text()
schedule = (role / "tasks/recovery-schedule.yml").read_text()
assert "'native' if wordpress_native" in schedule and "ansible.builtin.cron" in schedule
assert "recovery-schedule.yml" in main and "recovery-docker" not in main
# Day-2 actions reach a native guest through the shared resolver, which takes only the
# instance's own _+lab guest; an untagged same-name guest is never accepted.
target = yaml.safe_load((repo / "ansible/tasks/maintenance/resolve-app-target.yml").read_text())
owned = next(t for t in target if t.get("name") == "Resolve app target | Resolve the native instance's owned guest")
assert owned["ansible.builtin.include_tasks"].endswith("proxmox/resolve-instance-guest.yml")
check = next(t for t in target if t.get("name") == "Resolve app target | Require exactly one owned native guest")
assert "groups['lab_managed']" in str(check["ansible.builtin.assert"]["that"])
select = next(t for t in target if t.get("name") == "Resolve app target | Select the native guest")
assert select["ansible.builtin.set_fact"]["_rat_host"] == "{{ resolve_guest_hosts[0] }}"
restore = (role / "tasks/restore-guest.yml").read_text()
assert "wordpress_native | bool" in restore

print("wordpress native pins one release, renders its config and site and uses the shared backup and restore")
