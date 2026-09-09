#!/usr/bin/env bash
# Focused contract checks for issue #132: Frigate's dedicated Docker-on-LXC guest,
# continuous-write recording mount, shared devices, secret boundary, and recovery surface.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

need() {
    local path="$1" needle="$2"
    grep -Fq -- "$needle" "$repo/$path" || {
        echo "FAIL: $path does not contain: $needle" >&2
        exit 1
    }
}

absent() {
    local path="$1" needle="$2"
    if grep -Fq -- "$needle" "$repo/$path"; then
        echo "FAIL: $path unexpectedly contains: $needle" >&2
        exit 1
    fi
}

need ansible/playbooks/apps/frigate.yml "stack_name: \"{{ app_config.stack }}\""
need ansible/playbooks/apps/frigate.yml "attach_mounts_owner: \"{{ app_config.recording_storage.owner }}\""
need ansible/playbooks/apps/frigate.yml "attach-shared-device.yml"
need ansible/playbooks/apps/frigate.yml "attach-usb-passthrough-lxc.yml"
need ansible/playbooks/apps/frigate.yml "df, -Pk"
need ansible/playbooks/apps/frigate.yml "minimum_free_gb"
need ansible/roles/frigate/tasks/main.yml "mountpoint"
need ansible/playbooks/apps/frigate.yml "combine(_instance_config | default({}), recursive=True)"
absent ansible/playbooks/apps/frigate.yml "attach-pci-passthrough.yml"

need ansible/tasks/proxmox/attach-usb-passthrough-lxc.yml "usb_lxc_device.mapping"
need ansible/tasks/proxmox/attach-usb-passthrough-lxc.yml "mode: shared"
need ansible/tasks/proxmox/attach-usb-passthrough-lxc.yml "_usb_lxc_holders"
need ansible/tasks/proxmox/attach-usb-passthrough-lxc.yml "'_+lab' in _usb_lxc_tags"
need ansible/tasks/proxmox/attach-usb-passthrough-lxc.yml "attach-shared-device.yml"
need ansible/tasks/proxmox/detach-usb-passthrough-lxc.yml "detach-shared-device.yml"
need ansible/tasks/proxmox/detach-usb-passthrough-lxc.yml "failed_when: false"
need ansible/playbooks/apps/remove.yml "detach-usb-passthrough-lxc.yml"
need ansible/playbooks/apps/remove.yml "Detach Frigate shared iGPU binding"

need ansible/roles/frigate/tasks/main.yml "camera_credentials"
need ansible/roles/frigate/tasks/main.yml "mqtt_password"
need ansible/roles/frigate/tasks/main.yml "no_log: true"
need ansible/roles/frigate/tasks/main.yml "/api/version"
need ansible/roles/frigate/templates/docker-compose.yml.j2 "coral_usb_device_path"
need ansible/roles/frigate/templates/docker-compose.yml.j2 "igpu_render_node"
need ansible/roles/frigate/templates/docker-compose.yml.j2 "rtsp_host_port"
need ansible/roles/frigate/templates/docker-compose.yml.j2 "webrtc_host_port"
need ansible/roles/frigate/templates/docker-compose.yml.j2 "recordings_path"
absent ansible/roles/frigate/templates/docker-compose.yml.j2 "8554:8554"
absent ansible/roles/frigate/templates/docker-compose.yml.j2 "8555:8555/tcp"
absent ansible/roles/frigate/templates/docker-compose.yml.j2 "privileged: true"
absent ansible/roles/frigate/templates/docker-compose.yml.j2 "/dev/bus/usb:/dev/bus/usb"
absent config.example/apps/frigate.example.yml "password:"

need config.example/infrastructure.yml "frigate_storage:"
need config.example/proxmox.yml "frigate-coral"
need docs/specs/frigate-recovery.md "existing node storage"
need ansible/roles/frigate/README.md "recording mount"

python3 - "$repo" <<'PY'
import pathlib
import sys
import yaml

repo = pathlib.Path(sys.argv[1])
paths = [
    repo / "ansible/playbooks/apps/frigate.yml",
    repo / "ansible/vars/app-defaults/frigate.yml",
    repo / "config.example/apps/frigate.example.yml",
    repo / "catalog/applications.yml",
    repo / "rundeck/jobs/deploy-frigate.yaml",
]
for path in paths:
    with path.open(encoding="utf-8") as handle:
        assert yaml.safe_load(handle) is not None, f"{path} did not parse"

defaults = yaml.safe_load(
    (repo / "ansible/vars/app-defaults/frigate.yml").read_text(encoding="utf-8")
)["frigate_defaults"]
assert defaults["stack"] == "frigate"
assert defaults["app"]["igpu_render_node"] == ""
assert defaults["app"]["coral_usb_mapping"] == ""
assert defaults["app"]["recordings_path"]
assert defaults["app"]["rtsp_port"] == ""
assert defaults["app"]["webrtc_port"] == ""
assert defaults["app"]["retention_days"] > 0

catalog = yaml.safe_load(
    (repo / "catalog/applications.yml").read_text(encoding="utf-8")
)["applications"]["frigate"]
assert catalog["job"] == "deploy-frigate.yaml"
assert catalog["scope"] == "estate"
assert catalog["type"] == "Video Surveillance"

example = yaml.safe_load(
    (repo / "config.example/apps/frigate.example.yml").read_text(encoding="utf-8")
)
assert example["app"]["cameras"]["front"]["path"] == "/stream1"
assert "camera_credentials" not in example["app"]
assert "mqtt_password" not in example["app"].get("mqtt", {})

playbook = (repo / "ansible/playbooks/apps/frigate.yml").read_text(encoding="utf-8")
assert playbook.count("attach-shared-device.yml") == 1
assert playbook.count("attach-usb-passthrough-lxc.yml") == 1
assert "recording_storage.mounts" in playbook
assert "recording_storage.owner" in playbook
assert "coral_usb_device_path" in playbook
assert "app.port" in (repo / "rundeck/jobs/deploy-frigate.yaml").read_text(encoding="utf-8")
assert "deploy_{{ instance | default('_instance_unset') }}" in playbook

usb = (repo / "ansible/tasks/proxmox/attach-usb-passthrough-lxc.yml").read_text(encoding="utf-8")
assert usb.index("Assert the mapping is declared shared") < usb.index("List the node's containers")
assert "pvesh get /cluster/mapping/usb/" in usb
assert "vendor:product" in usb

print("Frigate Docker-on-LXC, mount ownership, device authority, secrets, and recovery: OK")
PY
