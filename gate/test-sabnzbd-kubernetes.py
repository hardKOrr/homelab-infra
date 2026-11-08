#!/usr/bin/env python3
"""Check SABnzbd's Docker-default and Kubernetes hosting contract."""

from pathlib import Path
import subprocess
import tempfile

import jinja2
import yaml


REPO = Path(__file__).resolve().parents[1]
ROLE = "ansible/roles/sabnzbd"


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
        instance="sabnzbd",
        app_config={"app": {"image": "example/sabnzbd:1", "port": 8085, "container_port": 8080,
                            "puid": 1313, "pgid": 1313, "storage": 1}},
        homelabinfra_config={"timezone": "UTC"},
        _sab_api_key="k" * 32,
        _sab_k8s_whitelist=["sabnzbd.example.test", "sabnzbd"],
        _sab_incomplete_dir="/mnt/media/down loads/incomplete",
        _sab_complete_dir="/mnt/media/down loads/complete",
        sabnzbd_fqdn="sabnzbd.example.test",
        k8s_media_volumes=[{"name": "media-0", "nfs": {"server": "192.0.2.5", "path": "/export/media",
                                                       "readOnly": False}}],
        k8s_media_volume_mounts=[{"name": "media-0", "mountPath": "/mnt/media"}],
    )
    return [d for d in yaml.safe_load_all(text) if d]


def main():
    defaults = read_yaml("ansible/vars/app-defaults/sabnzbd.yml")["sabnzbd_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert "kubernetes" in defaults["hosting"]["supported"]
    client = defaults["backup"]["client"]
    for key in ("image", "key_url", "repository", "suite", "component"):
        assert client.get(key), f"backup.client.{key} missing"

    plays = {play["name"]: play for play in read_yaml("ansible/playbooks/apps/sabnzbd.yml")}
    docker = plays["SABnzbd | Deploy"]
    assert any(
        task.get("ansible.builtin.include_role", {}).get("name") == "sabnzbd"
        and task.get("when") == "hostvars['localhost'].app_config.hosting.method in ['docker', 'native']"
        for task in docker["tasks"]
    )
    kube = plays["SABnzbd | Deploy on Kubernetes"]
    assert any(
        task.get("ansible.builtin.include_role", {}).get("name") == "sabnzbd"
        and task.get("when") == "app_config.hosting.method == 'kubernetes'"
        for task in kube["tasks"]
    )
    prepare = plays["SABnzbd | Prepare Kubernetes"]["tasks"]
    assert any(
        task.get("ansible.builtin.include_role", {}).get("tasks_from") == "resolve-kubernetes-media"
        for task in prepare
    )
    assert includes(prepare, "tasks/kubernetes/open-app-access.yml")
    # Every declared mount is resolved as an NFS share and mounted at its Docker path.
    resolve = read_yaml(f"{ROLE}/tasks/resolve-kubernetes-media.yml")
    share = next(t for t in resolve if "resolve-media-nfs.yml" in str(t.get("ansible.builtin.include_tasks")))
    assert share["vars"]["k8s_media_mounts"] == "{{ _sab_k8s_mounts }}"

    # The NFS validation runs before anything is written under the node's mount path.
    order = [str(t.get("ansible.builtin.include_tasks", "")) + str(t.get("ansible.builtin.file", ""))
             for t in resolve]
    nfs = next(i for i, x in enumerate(order) if "resolve-media-nfs.yml" in x)
    made = next(i for i, x in enumerate(order) if "directory" in x)
    assert nfs < made

    kubernetes = read_yaml(f"{ROLE}/tasks/kubernetes.yml")
    assert includes(kubernetes, "tasks/kubernetes/prepare-media-volumes.yml")
    assert includes(kubernetes, "tasks/kubernetes/apply-manifest.yml")
    volumes = next(t for t in kubernetes if "prepare-media-volumes.yml" in str(t.get("ansible.builtin.include_tasks")))
    assert "'mount_path': share.path" in volumes["vars"]["k8s_media_shares"]
    assert "'read_only': false" in volumes["vars"]["k8s_media_shares"]
    backup = next(t for t in kubernetes if "tasks/kubernetes/recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks")))
    claim = backup["vars"]["k8s_recovery"]["archives"][0]
    assert claim["name"] == "data" and claim["claim"] == "{{ instance }}-config"

    restore = read_yaml(f"{ROLE}/tasks/restore.yml")
    assert includes(restore, "restore-kubernetes.yml") and includes(restore, "restore-guest.yml")
    job = read_yaml(f"{ROLE}/tasks/restore-kubernetes.yml")[0]
    assert "tasks/kubernetes/recovery-restore.yml" in job["ansible.builtin.include_tasks"]
    assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"

    # The rendered workload: media at the Docker paths, config PVC, init container.
    kinds = {doc["kind"]: doc for doc in render_manifest()}
    assert set(kinds) == {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"}
    pod = kinds["Deployment"]["spec"]["template"]["spec"]
    app = pod["containers"][0]
    assert {"name": "media-0", "mountPath": "/mnt/media"} in app["volumeMounts"]
    assert any(v.get("nfs", {}).get("path") == "/export/media" for v in pod["volumes"])
    ingress = kinds["Ingress"]["spec"]["rules"][0]
    assert ingress["host"] == "sabnzbd.example.test"
    assert ingress["http"]["paths"][0]["backend"]["service"]["port"]["number"] == 8085

    # Exercise the init container's config edit: seed once, enforce the API key always, keep
    # the operator's settings, and report no change on a repeat.
    script = pod["initContainers"][0]["args"][0]
    key = "k" * 32
    with tempfile.TemporaryDirectory() as directory:
        run = script.replace("/config", directory)
        env = {"PUID": str(Path(directory).stat().st_uid), "PGID": str(Path(directory).stat().st_gid),
               "SAB_API_KEY": key, "PATH": "/usr/bin:/bin"}
        conf = Path(directory) / "sabnzbd.ini"

        def start():
            subprocess.run(["sh", "-c", run], check=True, env=env, capture_output=True)
            return conf.read_text()

        first = start()
        assert f"api_key = {key}" in first and "port = 8080" in first
        assert "host_whitelist = sabnzbd.example.test, sabnzbd" in first
        assert "download_dir = /mnt/media/down loads/incomplete" in first
        assert start() == first
        # A key regenerated in the UI is put back in place; other sections stay untouched.
        conf.write_text(first.replace(key, "other") + "[servers]\n[[news]]\napi_key = unrelated\n")
        again = start()
        assert again.count(f"api_key = {key}") == 1 and "api_key = other" not in again
        assert "[[news]]\napi_key = unrelated" in again
        # A file whose [misc] has no key gets one right after the header.
        conf.write_text("[misc]\nhost = 0.0.0.0\n[other]\nx = 1\n")
        assert start().startswith(f"[misc]\napi_key = {key}\nhost = 0.0.0.0\n")
        assert sorted(p.name for p in conf.parent.iterdir()) == ["sabnzbd.ini"]


if __name__ == "__main__":
    main()
