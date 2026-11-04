#!/usr/bin/env python3
"""Check ComfyUI's Docker default and its CPU-mode Kubernetes path on the shared operations."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/comfyui"


def read_yaml(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def main():
    defaults = read_yaml("ansible/vars/app-defaults/comfyui.yml")["comfyui_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes", "native-lxc", "native-vm"]
    assert defaults["app"]["storage"] > 0
    for key in ("image", "key_url", "repository", "suite", "component"):
        assert defaults["backup"]["client"].get(key), f"backup.client.{key} missing"

    plays = {play["name"]: play for play in read_yaml("ansible/playbooks/apps/comfyui.yml")}
    provision = plays["ComfyUI | Provision"]["tasks"]
    assert all(
        "docker" in str(task.get("when")) for task in provision
        if task.get("name", "").startswith(("Provision ComfyUI", "Find or create"))
    )
    assert includes(plays["ComfyUI | Prepare Kubernetes"]["tasks"], "tasks/kubernetes/open-app-access.yml")
    deploy = plays["ComfyUI | Deploy"]["tasks"]
    assert any(
        task.get("ansible.builtin.include_role", {}).get("name") == "comfyui"
        and task.get("when") == "hostvars['localhost'].app_config.hosting.method in ['docker', 'native']"
        for task in deploy
    )
    assert any(
        task.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
        and task.get("when") == "app_config.hosting.method == 'kubernetes'"
        for task in plays["ComfyUI | Deploy on Kubernetes"]["tasks"]
    )

    tasks = read_yaml("ansible/roles/comfyui/tasks/kubernetes.yml")
    assert "cpu" in str(tasks[0]["ansible.builtin.assert"]["that"])
    assert includes(tasks, "tasks/kubernetes/apply-manifest.yml")
    backup = next(t for t in tasks if "tasks/kubernetes/recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks")))
    archive = backup["vars"]["k8s_recovery"]["archives"][0]
    assert archive == {"name": "data", "claim": "{{ instance }}-storage"}, archive

    restore = read_yaml("ansible/roles/comfyui/tasks/restore.yml")
    assert includes(restore, "restore-kubernetes.yml") and includes(restore, "restore-guest.yml")
    job = read_yaml("ansible/roles/comfyui/tasks/restore-kubernetes.yml")[0]
    assert "tasks/kubernetes/recovery-restore.yml" in job["ansible.builtin.include_tasks"]
    assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-storage"

    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    text = env.from_string((ROLE / "templates/manifest.yaml.j2").read_text(encoding="utf-8")).render(
        instance="comfy", comfyui_fqdn="comfy.example.test", comfyui_run_uid=1000, comfyui_run_gid=1111,
        homelabinfra_config={}, app_config={"app": {"image": "img@sha256:x", "port": 8188, "storage": 50}},
    )
    docs = {(d["kind"]): d for d in yaml.safe_load_all(text) if d}
    assert set(docs) == {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"}
    pvc = docs["PersistentVolumeClaim"]
    assert pvc["metadata"]["name"] == "comfy-storage"
    assert "storageClassName" not in pvc["spec"], "must use the cluster default StorageClass"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "50Gi"
    pod = docs["Deployment"]["spec"]["template"]["spec"]
    assert pod["enableServiceLinks"] is False, "Service links override AI-Dock's COMFYUI_PORT"
    mounts = {m["subPath"]: m["mountPath"] for m in pod["containers"][0]["volumeMounts"]}
    assert mounts == {
        "models": "/workspace/storage/models", "user": "/opt/ComfyUI/user",
        "custom_nodes": "/workspace/storage/custom_nodes",
        "input": "/workspace/storage/input", "output": "/workspace/storage/output",
    }, mounts
    volumes = {v["name"]: v for v in pod["volumes"]}
    for container in pod["initContainers"] + pod["containers"]:
        for mount in container["volumeMounts"]:
            assert volumes[mount["name"]]["persistentVolumeClaim"]["claimName"] == "comfy-storage"
    assert "nvidia" not in text and "gpu" not in text.lower()
    assert docs["Ingress"]["spec"]["rules"][0]["host"] == "comfy.example.test"
    print("ComfyUI Kubernetes hosting passed.")


if __name__ == "__main__":
    main()
