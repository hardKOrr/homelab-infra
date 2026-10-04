#!/usr/bin/env python3
"""Bounded PBS proof orchestration. Provider mutations belong to existing playbooks."""

from __future__ import annotations

import copy
import json
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from coverage import load_inputs, _hosting, _product

ROOT = Path(__file__).resolve().parents[3]
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class Refused(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else value
        )
    return result


def resolve(request, inputs):
    """Resolve the owning product route, never an operator-supplied method override."""
    app, instance = request.get("app", ""), request.get("instance", "")
    require(
        app in inputs and SAFE.fullmatch(instance or ""),
        "Select a catalog app and exact instance.",
    )
    require(
        re.fullmatch(r"[1-9][0-9]*", str(request.get("vmid", ""))),
        "An exact disposable guest VMID is required.",
    )
    item = inputs[app]
    defaults = item["defaults"]
    hosting, _ = _hosting(app, defaults)
    row = _product(app, item, inputs, audit=None, native_evidence=None)
    methods = defaults.get("recovery", {}).get("methods", [])
    actual = (
        "native"
        if "native" in methods
        else "project_managed" if "project_managed" in methods else "pbs_guest"
    )
    require(
        request.get("method", "auto") in ("auto", actual),
        "Requested method conflicts with the owning declaration.",
    )
    require(
        actual == "pbs_guest" and row["fallback"]["disposition"] == "pbs-guest-only",
        "Only a declared PBS guest recovery unit is implemented; native/project-managed/rebuild are refused.",
    )
    require(
        hosting in ("native", "docker")
        and app not in ("pbs", "vaultwarden", "k3s-cluster"),
        "Cluster, appliance and out-of-band control-plane recovery are refused.",
    )
    require(
        request.get("destination", "existing") in ("existing", "new"),
        "Destination must be existing or new.",
    )
    require(
        request.get("execute") in (True, False) and request.get("disposable") is True,
        "Explicit disposable=true authority is required even for planning.",
    )
    require(
        SAFE.fullmatch(request.get("backup_storage", "")), "Select a PBS storage ID."
    )
    require(
        SAFE.fullmatch(request.get("target_storage", "")), "Select destination storage."
    )
    require(
        isinstance(defaults.get("app", {}).get("port"), int)
        and 0 < defaults["app"]["port"] < 65536,
        "Product needs a declared HTTP serving port before drill mutation.",
    )
    adapter = defaults.get("recovery", {}).get("drill", {})
    require(set(adapter) <= {"fixture_playbook"}, "Unknown drill adapter declaration.")
    fixture = adapter.get("fixture_playbook")
    if fixture:
        require(
            fixture.startswith("playbooks/maintenance/")
            and ".." not in Path(fixture).parts
            and (ROOT / "ansible" / fixture).is_file(),
            "Fixture must be a repository-owned maintenance playbook.",
        )
    return row, fixture, defaults


def interface(value):
    """PVE property strings may reorder fields and normalize MAC case on write."""
    fields = dict(part.split("=", 1) for part in value.split(",") if "=" in part)
    if "hwaddr" in fields:
        fields["hwaddr"] = fields["hwaddr"].lower()
    return fields


def validate_guest(request, observation, defaults):
    guest, config = observation["guest"], observation["config"]
    tags = set(guest.get("tags", "").split(";"))
    workloads = sorted(
        t[1:] for t in tags if re.fullmatch(r"_[A-Za-z0-9][A-Za-z0-9_.-]*", t)
    )
    acknowledged = sorted(filter(None, request.get("workloads", "").split(",")))
    require(
        str(guest["vmid"]) == str(request["vmid"])
        and guest["type"] in ("qemu", "lxc")
        and not guest.get("template"),
        "Select an exact non-template VM/LXC.",
    )
    require(
        "_+lab" in config.get("tags", "").split(";"),
        "Guest configuration ownership must agree with inventory.",
    )
    require(
        len(observation.get("units", [])) == 1
        and str(observation["units"][0]["vmid"]) == str(request["vmid"]),
        "Instance/stack selector must resolve uniquely to the explicitly scoped VMID.",
    )
    require(
        "_+lab" in tags and guest.get("status") == "running",
        "Guest must be owned and running before the drill.",
    )
    require(workloads, "Guest workload records are missing; no mutation is authorized.")
    if request.get("execute"):
        require(
            workloads == acknowledged,
            "Acknowledge every workload tag with the exact comma-separated workloads list.",
        )
    require(
        not any(
            t.startswith(("_.cluster+", "_.dev+"))
            or t in ("_-k3s", "_rundeck", "_pbs", "_vaultwarden")
            for t in tags
        ),
        "Cluster, device, runner, vault and PBS guests are refused.",
    )
    stack = defaults.get("stack", "")
    require(
        ("_.stack+" + stack in tags) if stack else ("_" + request["instance"] in tags),
        "Application does not resolve to the explicitly selected guest.",
    )
    for key, value in config.items():
        require(
            not re.match(r"^(hostpci|usb|dev|hookscript)", key),
            "External devices/hooks are outside PBS coverage.",
        )
        if re.match(r"^(mp|scsi|sata|virtio|ide)\d+$", key):
            require(
                not str(value).startswith("/")
                and "backup=0" not in str(value)
                and "media=cdrom" not in str(value),
                "Excluded/external disks or mounts need a separate recovery declaration.",
            )
    require(
        any(
            row.get("storage") == request["backup_storage"]
            and row.get("type") == "pbs"
            and row.get("active") == 1
            for row in observation.get("source_storages", [])
        ),
        "Backup storage must be an active PBS backend before mutation.",
    )
    require(
        request.get("destination", "existing") != "existing"
        or not request.get("target_node")
        or request["target_node"] == guest["node"],
        "Existing destination must use the scoped guest node.",
    )
    require(
        any(
            row.get("storage") == request["target_storage"] and row.get("active") == 1
            for row in observation.get("storages", [])
        ),
        "Destination storage must be active before deployment/backup mutation.",
    )
    require(
        any(re.fullmatch(r"net\d+", key) for key in config),
        "Guest interface identity is incomplete.",
    )
    if request.get("destination", "existing") == "new":
        require(
            re.fullmatch(r"[1-9][0-9]*", str(request.get("target_vmid", "")))
            and str(request["target_vmid"]) != str(request["vmid"]),
            "New destination needs a distinct unused VMID.",
        )
        require(
            SAFE.fullmatch(request.get("target_name", ""))
            and SAFE.fullmatch(request.get("target_node", "")),
            "New destination needs explicit target name/node.",
        )
        require(
            "_+lab" in request.get("target_tags", "").split(";"),
            "New destination needs ownership tags.",
        )
        require(
            not observation.get("targets") and not observation.get("target_names"),
            "New VMID or name already exists; no mutation authorized.",
        )
        new_tags = set(request["target_tags"].split(";"))
        require(
            "_" + request["target_name"] in new_tags
            and not (new_tags & {"_" + name for name in workloads})
            and not any(tag.startswith(("_.stack+", "_.cluster+")) for tag in new_tags),
            "New tags must identify only the isolated target, without source workload/stack/cluster selectors.",
        )
        interfaces = [v for k, v in config.items() if re.fullmatch(r"net\d+", k)]
        require(
            len(interfaces) == 1
            and not any(re.fullmatch(r"ipconfig\d+", k) for k in config),
            "Owning new route cannot isolate multiple interfaces or replace VM cloud-init addresses; refuse this source layout.",
        )
        source_parts = interface(interfaces[0])
        target_parts = interface(request["target_network"])
        require(
            SAFE.fullmatch(target_parts.get("name", ""))
            and SAFE.fullmatch(target_parts.get("bridge", "")),
            "New LXC interface needs explicit safe name and bridge fields.",
        )
        require(
            target_parts.get("link_down") == "1",
            "New interface must be explicitly isolated with link_down=1.",
        )
        try:
            source_ip = ipaddress.ip_interface(source_parts.get("ip", "")).ip
            target_ip = ipaddress.ip_interface(target_parts.get("ip", "")).ip
        except ValueError:
            raise Refused(
                "New source/destination addresses must be explicit IP interfaces; DHCP/unknown layouts are refused."
            ) from None
        require(
            target_ip != source_ip,
            "New target needs a distinct explicit address; no source address inheritance.",
        )
        target_mac = target_parts.get("hwaddr", "").lower()
        require(
            re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", target_mac)
            and target_mac != source_parts.get("hwaddr", "").lower(),
            "New target needs a distinct explicit MAC.",
        )
        require(
            request["target_name"] != config.get("name", config.get("hostname")),
            "New target name must differ from source.",
        )
    return workloads


def identity(observation):
    config = observation["config"]
    result = {
        key: value
        for key, value in config.items()
        if key in ("name", "hostname", "tags")
        or re.fullmatch(r"(net|ipconfig)\d+", key)
    }
    result["onboot"] = int(config.get("onboot", 0))
    return result


def fresh_artifact(before, after, vmid, kind):
    def native(volid):
        # Remove only an optional storage identifier, never timestamp colons.
        return re.sub(r"^[A-Za-z0-9][A-Za-z0-9_.-]*:", "", volid)

    old = {native(row["volid"]) for row in before["artifacts"]}
    pattern = rf"backup/{'vm' if kind == 'qemu' else 'ct'}/{re.escape(str(vmid))}/[^/]+"
    candidates = [
        native(row["volid"])
        for row in after["artifacts"]
        if native(row["volid"]) not in old
        and re.fullmatch(pattern, native(row["volid"]))
    ]
    require(
        len(candidates) == 1,
        "Backup must expose exactly one newly completed native artifact; retain points and retry after inventory review.",
    )
    return candidates[0]


class Ansible:
    """Run the owning playbook directly, inheriting the execution-private environment."""

    def __init__(self, request):
        self.request = request
        self.warnings = False

    def run(self, playbook, **values):
        require(
            (ROOT / "ansible" / playbook).is_file(), "Owning playbook is unavailable."
        )
        with tempfile.TemporaryDirectory(prefix="homelab-proof-") as temporary:
            path = Path(temporary) / "inputs.json"
            path.write_text(json.dumps(dict(self.request, **values)))
            path.chmod(0o600)
            env = dict(
                os.environ,
                ANSIBLE_STDOUT_CALLBACK="recovery_proof",
                ANSIBLE_CALLBACKS_ENABLED="",
                ANSIBLE_NOCOLOR="1",
            )
            result = subprocess.run(
                [
                    str(Path(sys.executable).with_name("ansible-playbook")),
                    "-i",
                    "inventory/",
                    playbook,
                    "-e",
                    "@" + str(path),
                ],
                cwd=ROOT / "ansible",
                env=env,
                capture_output=True,
                text=True,
                timeout=10800,
            )
            self.warnings |= bool(result.stderr.strip())
            try:
                report = json.loads(result.stdout)
            except (ValueError, TypeError):
                raise Refused(
                    "Owning playbook did not return a complete observation; inspect its execution privately."
                ) from None
            require(
                result.returncode == 0
                and not report["failed"]
                and all(
                    not c.get("failures")
                    and not c.get("unreachable")
                    and not c.get("rescued")
                    and not c.get("ignored")
                    for c in report["stats"].values()
                ),
                "Owning playbook failed or reported degraded evidence; retained artifacts require inspection before retry.",
            )
            return report

    def inspect(self, **values):
        return self.run("playbooks/maintenance/proof-inspect.yml", **values)[
            "observations"
        ][0]


def dispatch(request, inputs, backend):
    evidence = {
        "complete": False,
        "durable_data": False,
        "live_verified": False,
        "stage": "resolution",
        "artifacts": {},
        "retry": "Retain A and B. Inspect failed-target state; use Restore Guest plan with B before retrying A. No cleanup or automatic retry.",
    }
    try:
        row, fixture, defaults = resolve(request, inputs)
        selector = (
            "_.stack+" + defaults["stack"]
            if defaults.get("stack")
            else "_" + request["instance"]
        )
        before = backend.inspect(proof_selector=selector)
        workloads = validate_guest(request, before, defaults)
        evidence.update(
            method="pbs_guest",
            recovery_unit=row["methods"]["pbs_guest"]["recovery_unit"],
            workloads=workloads,
            guest_vmid=str(request["vmid"]),
            guest_node=before["guest"]["node"],
            target_vmid=(
                str(request["vmid"])
                if request.get("destination", "existing") == "existing"
                else str(request.get("target_vmid", ""))
            ),
            backup_storage=request["backup_storage"],
            target_storage=request["target_storage"],
            destination=request.get("destination", "existing"),
            onboot=int(before["config"].get("onboot", 0)),
            scope_acknowledged=workloads
            == sorted(filter(None, request.get("workloads", "").split(","))),
            data_claim=(
                "declared fixture only; external data/dependencies unverified"
                if fixture
                else "none; serving-only"
            ),
            fixture="application-cache" if fixture else "serving-only",
            limitations=[
                "Guest-mounted remote filesystems and external dependencies remain independently unverified.",
                "Serving-only evidence is incomplete for durable-data recovery.",
            ],
            sequence=[
                "deploy",
                "converge",
                "fixture A/read",
                "backup A/verify",
                "fixture B/read",
                "backup B/verify",
                "restore A via Restore Guest",
                "identity/interfaces/onboot",
                "serving/A-present/B-absent",
            ],
        )
        if not fixture:
            evidence["sequence"] = [
                "deploy",
                "converge",
                "serving",
                "backup A/verify",
                "backup B/verify (no fixture mutation)",
                "restore A via Restore Guest",
                "identity/interfaces/onboot",
                "serving-only; durable-data incomplete",
            ]
        if not request.get("execute"):
            evidence.update(stage="plan", mutation=False)
            return evidence, 0
        evidence["stage"] = "deploy"
        backend.run(f"playbooks/apps/{request['app']}.yml")
        convergence = backend.run(f"playbooks/apps/{request['app']}.yml")
        require(
            convergence["stats"]
            and sum(c.get("changed", 0) for c in convergence["stats"].values()) == 0,
            "Second deployment did not converge; no fixture or backup/recovery mutation followed.",
        )
        baseline = backend.inspect(proof_selector=selector)
        validate_guest(request, baseline, defaults)
        require(
            identity(before) == identity(baseline),
            "Deployment changed scoped guest identity; re-plan before recovery.",
        )
        serving = backend.run(
            "playbooks/maintenance/proof-serving.yml",
            proof_port=defaults["app"]["port"],
        )["observations"][0]
        require(serving.get("serving") is True, "Application serving assertion failed.")
        run = uuid.uuid4().hex
        fixture_vars = dict(
            proof_run=run,
            proof_topic=defaults["app"].get("topic", "recovery-proof"),
            proof_url=serving["url"],
        )
        if fixture:
            evidence["stage"] = "fixture-A"
            written_a = backend.run(fixture, proof_phase="A", **fixture_vars)[
                "observations"
            ][0]
            require(
                written_a.get("a_present") is True,
                "Adapter did not verify fixture A through the application; no backup follows.",
            )
        evidence["stage"] = "backup-A"
        backend.run("playbooks/maintenance/backup-guest.yml", method="pbs_guest")
        a = backend.inspect(proof_selector=selector)
        evidence["artifacts"]["A"] = fresh_artifact(
            baseline, a, request["vmid"], baseline["guest"]["type"]
        )
        backend.run(
            "playbooks/maintenance/proof-artifact.yml",
            recovery_point=evidence["artifacts"]["A"],
            proof_node=baseline["guest"]["node"],
        )
        if fixture:
            evidence["stage"] = "fixture-B"
            written_b = backend.run(fixture, proof_phase="B", **fixture_vars)[
                "observations"
            ][0]
            require(
                written_b.get("a_present") is True
                and written_b.get("b_present") is True,
                "Adapter did not verify distinguishable A and B; no recovery follows.",
            )
        evidence["stage"] = "backup-B"
        backend.run("playbooks/maintenance/backup-guest.yml", method="pbs_guest")
        b = backend.inspect(proof_selector=selector)
        evidence["artifacts"]["B"] = fresh_artifact(
            a, b, request["vmid"], baseline["guest"]["type"]
        )
        backend.run(
            "playbooks/maintenance/proof-artifact.yml",
            recovery_point=evidence["artifacts"]["B"],
            proof_node=baseline["guest"]["node"],
        )
        require(
            evidence["artifacts"]["A"] != evidence["artifacts"]["B"],
            "A and B must be independent points.",
        )
        destination = request.get("destination", "existing")
        restore = dict(
            method="pbs_guest",
            source_vmid=request["vmid"],
            destination=destination,
            target_vmid=(
                request["vmid"] if destination == "existing" else request["target_vmid"]
            ),
            recovery_point=evidence["artifacts"]["A"],
            pre_restore_point=evidence["artifacts"]["B"],
        )
        evidence["stage"] = "restore-plan"
        backend.run(
            "playbooks/maintenance/restore-guest.yml", overwrite=False, **restore
        )
        evidence["stage"] = "restore-execute"
        backend.run(
            "playbooks/maintenance/restore-guest.yml", overwrite=True, **restore
        )
        if destination == "new":
            new = backend.inspect(vmid=request["target_vmid"])
            require(
                new["guest"]["status"] == "stopped"
                and int(new["config"].get("onboot", 0)) == 0
                and new["config"].get("name", new["config"].get("hostname"))
                == request["target_name"]
                and interface(new["config"].get("net0", ""))
                == interface(request["target_network"])
                and set(new["config"].get("tags", "").split(";"))
                == set(request["target_tags"].split(";")),
                "New target isolation/identity/onboot verification failed; inspect retained artifacts.",
            )
            evidence.update(
                stage="isolated-new",
                serving=False,
                durable_data=False,
                target_state="stopped; onboot=0; link_down=1",
                pending="Activation and application data/serving checks require separately authorized isolated observation. Only destination inspection follows the new restore; no source reads.",
            )
            return evidence, 0
        evidence["stage"] = "verify"
        restored = backend.inspect(proof_selector=selector)
        require(
            restored["guest"]["status"] == "running"
            and identity(restored) == identity(baseline),
            "Restored running state/identity/interfaces/onboot differ; inspect target and retained B before retry.",
        )
        report = (
            backend.run(fixture, proof_phase="verify", **fixture_vars)
            if fixture
            else backend.run(
                "playbooks/maintenance/proof-serving.yml",
                proof_port=defaults["app"]["port"],
            )
        )
        assertions = report["observations"][0]
        require(
            assertions.get("serving") is True, "Restored application is not serving."
        )
        if fixture:
            require(
                assertions.get("a_present") is True
                and assertions.get("b_absent") is True,
                "Fixture must prove A-present and B-absent.",
            )
        evidence.update(
            stage="verified" if fixture else "serving-only",
            complete=bool(fixture) and not getattr(backend, "warnings", False),
            sequence_complete=True,
            durable_data=bool(fixture),
            serving=True,
            assertions=["running", "identity", "interfaces", "onboot", "serving"]
            + (["A-present", "B-absent"] if fixture else []),
        )
        return evidence, 0
    except (
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
        OSError,
        subprocess.TimeoutExpired,
    ) as error:
        # Unexpected/provider diagnostics may contain secrets. Only our fixed refusals escape.
        evidence["failure"] = (
            str(error)
            if isinstance(error, Refused)
            else "Incomplete observation or execution; inspect privately before retry."
        )
        evidence["target_state"] = "unknown; inspect before retry; no cleanup performed"
        return evidence, 1
    finally:
        evidence["warnings_present"] = getattr(backend, "warnings", False)


def main():
    request = json.load(sys.stdin)
    inputs, _, _ = load_inputs()
    app = request.get("app")
    if app in inputs:
        import yaml

        config = ROOT / "config" / "apps" / f"{request.get('instance', '')}.yml"
        require(
            SAFE.fullmatch(request.get("instance", "")), "Invalid instance selector."
        )
        # Runtime instance layering changes placement, never repository-owned drill adapters
        # or the owning method selection. A conflicting recovery override is refused.
        if config.is_file():
            overlay = yaml.safe_load(config.read_text()) or {}
            require(
                "recovery" not in overlay,
                "Recovery method/adapters are repository-owned declarations.",
            )
            inputs[app]["defaults"] = merge(inputs[app]["defaults"], overlay)
    report, status = dispatch(request, inputs, Ansible(request))
    print(json.dumps(report))
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print(
            json.dumps(
                {
                    "complete": False,
                    "durable_data": False,
                    "failure": "Invalid dispatcher input or declaration; no recovery mutation authorized.",
                }
            )
        )
        sys.exit(1)
