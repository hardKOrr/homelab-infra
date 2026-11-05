#!/usr/bin/env python3
"""A restored native app.ini gets the target's database, hostname, ports and paths."""
import configparser
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"

SOURCE = """APP_NAME = src
[security]
SECRET_KEY = keep-me
[database]
HOST = src-db:5432
NAME = srcdb
USER = srcuser
PASSWD = srcpass
[server]
DOMAIN = src.example.test
ROOT_URL = https://src.example.test/
SSH_DOMAIN = src.example.test
SSH_PORT = 2222
SSH_LISTEN_PORT = 2222
HTTP_PORT = 3000
"""


def main():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        ini = base / "data/gitea/conf/app.ini"
        ini.parent.mkdir(parents=True)
        ini.write_text(SOURCE)
        play = base / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {
                "forgejo_native_user": subprocess.run(["id", "-un"], text=True, capture_output=True).stdout.strip(),
                "forgejo_app_ini": str(ini), "forgejo_data_path": str(base / "data"),
                "forgejo_fqdn": "dst.example.test",
                "app_config": {"app": {"port": 3100, "ssh_port": 2322}},
                "forgejo_ini_database": {"host": "dst-db", "port": 5433, "name": "dstdb",
                                         "user": "dstuser", "password": "dstpass"}},
            "handlers": [{"name": "Restart Forgejo", "ansible.builtin.debug": {"msg": "restart"}}],
            "tasks": [{"ansible.builtin.include_tasks": str(ROOT / "ansible/roles/forgejo/tasks/app-ini.yml")}],
        }]))
        result = subprocess.run([str(ANSIBLE), "-i", "localhost,", "-c", "local", str(play)],
                                text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string("[DEFAULT]\n" + ini.read_text())
        assert cfg["database"]["HOST"] == "dst-db:5433" and cfg["database"]["NAME"] == "dstdb"
        assert cfg["database"]["USER"] == "dstuser" and cfg["database"]["PASSWD"] == "dstpass"
        server = cfg["server"]
        assert server["DOMAIN"] == server["SSH_DOMAIN"] == "dst.example.test"
        assert server["ROOT_URL"] == "https://dst.example.test/"
        assert server["HTTP_PORT"] == "3100" and server["SSH_PORT"] == server["SSH_LISTEN_PORT"] == "2322"
        assert cfg["security"]["SECRET_KEY"] == "keep-me"
        assert cfg["DEFAULT"]["WORK_PATH"] == str(base / "data")
    print("Forgejo app.ini reconciliation checks passed")


main()
