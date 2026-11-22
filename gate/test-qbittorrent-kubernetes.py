#!/usr/bin/env python3
"""Check qBittorrent's Docker-default and Kubernetes hosting contract."""

from pathlib import Path
import subprocess
import tempfile

import jinja2
import yaml


REPO = Path(__file__).resolve().parents[1]
ROLE = "ansible/roles/qbittorrent"


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def render_manifest():
    env = jinja2.Environment(extensions=["jinja2.ext.do"], keep_trailing_newline=True)
    env.filters["to_json"] = lambda v: __import__("json").dumps(v)
    env.filters["quote"] = lambda v: __import__("shlex").quote(str(v))
    env.filters["hash"] = lambda v, _algo: __import__("hashlib").sha256(str(v).encode()).hexdigest()
    template = env.from_string((REPO / ROLE / "templates/manifest.yaml.j2").read_text())
    text = template.render(
        instance="qbittorrent",
        app_config={"app": {"image": "example/qbittorrent:1", "port": 8080, "container_port": 8080,
                            "puid": 1313, "pgid": 1313, "storage": 1, "torrenting_port": 6881,
                            "download_path": "/mnt/media/down loads"}},
        homelabinfra_config={"timezone": "UTC"},
        qbittorrent_peer_outgoing_ports_min=61000,
        qbittorrent_peer_outgoing_ports_max=65535,
        _qbt_password_hash="@ByteArray(c2FsdA==:a2V5)",
        qbittorrent_fqdn="qbittorrent.example.test",
        k8s_media_volumes=[{"name": "media-0", "nfs": {"server": "192.0.2.5", "path": "/export/media",
                                                       "readOnly": False}}],
        k8s_media_volume_mounts=[{"name": "media-0", "mountPath": "/mnt/media"}],
    )
    return [d for d in yaml.safe_load_all(text) if d]


def main():
    defaults = read_yaml("ansible/vars/app-defaults/qbittorrent.yml")["qbittorrent_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    client = defaults["backup"]["client"]
    for key in ("image", "key_url", "repository", "suite", "component"):
        assert client.get(key), f"backup.client.{key} missing"

    # Deploy runs through playbooks/apps/deploy.yml (gate/app-matrix.yml); the Kubernetes
    # shares are resolved by the role's deploy-prepare hook, and kubernetes.yml runs main.yml.
    prepare = read_yaml(f"{ROLE}/tasks/deploy-prepare.yml")
    assert includes(prepare, "resolve-kubernetes-media.yml")
    entry = read_yaml(f"{ROLE}/tasks/kubernetes.yml")
    assert entry[0]["ansible.builtin.include_tasks"] == "main.yml"
    refuse = next(t for t in prepare if "ansible.builtin.assert" in t)
    assert refuse["when"] == "app_config.hosting.method == 'kubernetes'"
    condition = " ".join(refuse["ansible.builtin.assert"]["that"].split())
    for inbound, ok in (([], True), ([{"name": "bittorrent-listen", "enabled": False}], True),
                        ([{"name": "bittorrent-listen", "enabled": True}], False)):
        env = jinja2.Environment()
        assert env.compile_expression(condition)(app_config={"firewall": {"inbound": inbound}}) is ok, inbound
    # Docker and native: the BitTorrent forward targets the port the client listens on.
    handoff = next(t for t in prepare if "ansible.builtin.set_fact" in t)["ansible.builtin.set_fact"]
    assert "app_config.app.torrenting_port" in handoff["firewall_inbound_ports"]["bittorrent-listen"]

    # Every declared mount is resolved as an NFS share and mounted at its Docker path.
    resolve = read_yaml(f"{ROLE}/tasks/resolve-kubernetes-media.yml")
    share = next(t for t in resolve if "resolve-media-nfs.yml" in str(t.get("ansible.builtin.include_tasks")))
    assert share["vars"]["k8s_media_mounts"] == "{{ _qbt_k8s_mounts }}"

    kubernetes = read_yaml(f"{ROLE}/tasks/kubernetes-workload.yml")
    volumes = next(t for t in kubernetes if "prepare-media-volumes.yml" in str(t.get("ansible.builtin.include_tasks")))
    assert "'mount_path': share.path" in volumes["vars"]["k8s_media_shares"]
    assert "'read_only': false" in volumes["vars"]["k8s_media_shares"]
    backup = next(t for t in kubernetes if "tasks/kubernetes/recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks")))
    claim = backup["vars"]["k8s_recovery"]["archives"][0]
    assert claim["name"] == "data" and claim["claim"] == "{{ instance }}-config"

    restore = read_yaml(f"{ROLE}/tasks/restore.yml")
    assert includes(restore, "restore-kubernetes.yml") and includes(restore, "restore-guest.yml")
    job = read_yaml(f"{ROLE}/tasks/restore-kubernetes.yml")[0]
    assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"

    # The rendered workload: media at the Docker paths, config PVC, init container.
    kinds = {doc["kind"]: doc for doc in render_manifest()}
    assert set(kinds) == {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"}
    pod = kinds["Deployment"]["spec"]["template"]["spec"]
    app = pod["containers"][0]
    assert {"name": "media-0", "mountPath": "/mnt/media"} in app["volumeMounts"]
    assert any(v.get("nfs", {}).get("path") == "/export/media" for v in pod["volumes"])
    assert {"name": "TORRENTING_PORT", "value": "6881"} in app["env"]

    # Exercise the init container's config edit: seed once, enforce the credential and the
    # peer ports always, keep the operator's settings, and report no change on a repeat.
    script = pod["initContainers"][0]["args"][0]
    with tempfile.TemporaryDirectory() as directory:
        run = script.replace("/config", directory)
        env = {"PUID": str(Path(directory).stat().st_uid), "PGID": str(Path(directory).stat().st_gid),
               "WEBUI_USER": "admin", "WEBUI_HASH": "@ByteArray(c2FsdA==:a2V5)", "PATH": "/usr/bin:/bin"}
        conf = Path(directory) / "qBittorrent/qBittorrent.conf"

        def start():
            subprocess.run(["sh", "-c", run], check=True, env=env, capture_output=True)
            return conf.read_text()

        first = start()
        assert "Session\\DefaultSavePath=/mnt/media/down loads" in first.replace("'", "")
        assert 'WebUI\\Password_PBKDF2="@ByteArray(c2FsdA==:a2V5)"' in first
        assert "Session\\OutgoingPortsMin=61000" in first and "Session\\OutgoingPortsMax=65535" in first
        assert start() == first
        conf.write_text(
            first.replace("WebUI\\Username=admin", "WebUI\\Username=other").replace("OutgoingPortsMax=65535", "OutgoingPortsMax=1")
            + "[Canary]\nkeep=me\n"
        )
        again = start()
        assert "WebUI\\Username=admin" in again and "OutgoingPortsMax=65535" in again
        assert "[Canary]\nkeep=me" in again and again.count("WebUI\\Username") == 1
        assert sorted(p.name for p in conf.parent.iterdir()) == ["qBittorrent.conf"]


if __name__ == "__main__":
    main()
