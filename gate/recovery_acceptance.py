#!/usr/bin/env python3
"""Synthetic two-destination recovery protocol and coverage rollup.

The protocol is intentionally provider-free. It models the boundaries that every
recovery adapter must preserve, while the product roles and the PBS guest route remain
the source of truth for their implementation. The state kept by a fixture is small,
named test state; it is never emitted as backup contents or as a credential-bearing
artifact.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Callable

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "ansible" / "vars" / "app-defaults"
CATALOG = ROOT / "catalog" / "applications.yml"

SUPPORTED_METHODS = ("pbs_guest", "native", "project_managed")

APPLICATION_ASSERTIONS = {
    "actual-budget": ("budget-record", "budget-file", "application-login", "target-route"),
    "comfyui": ("workflow-record", "model-file", "target-route", "gpu-rebind"),
    "hi-events": ("event-record", "uploaded-asset", "organizer-login", "target-route"),
    "home-assistant": ("entity-state", "config-file", "user-login", "usb-rebind"),
    "immich": ("photo-record", "original-file", "user-login", "database-and-media"),
    "jellyseerr": ("request-record", "config-file", "user-login", "arr-connections"),
    "maintainerr": ("rule-record", "config-file", "user-login", "arr-connections"),
    "mixpost": ("post-record", "upload-file", "encryption-key", "database-connection"),
    "ollama": ("model-record", "model-file", "target-route", "gpu-rebind"),
    "open-webui": ("chat-record", "upload-file", "user-login", "ollama-connection"),
    "odoo": ("crm-record", "filestore-document", "database-connection", "admin-login"),
    "n8n": ("workflow-state", "credential-state", "encryption-key", "database-connection"),
    "plane": ("project-record", "server-worker-state", "object-storage", "database-and-redis"),
}


class ProtocolFailure(RuntimeError):
    """A failure that must be visible to the acceptance protocol."""


@dataclass(frozen=True)
class MethodCase:
    product: str
    method: str
    kind: str
    version: str
    artifact_prefix: str
    assertions: tuple[str, ...]
    source_file: str
    issue_reference: int | None = None
    affected_scope: tuple[str, ...] = ("fixture-app", "fixture-backend")

    @property
    def case_id(self) -> str:
        return f"{self.product}:{self.method}:{self.kind}"


@dataclass
class Fixture:
    name: str
    kind: str
    method: str
    version: str
    storage_owner: str
    route: str
    identity: str
    state: dict[str, Any]
    external_state: dict[str, Any]
    connections: dict[str, str]
    affected_scope: tuple[str, ...]
    isolated: bool = True
    reachable: bool = True
    active: bool = True
    complete: bool = False
    source_accesses: int = 0
    production_effects: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RecoveryPoint:
    artifact_id: str
    method: str
    kind: str
    source: str
    version: str
    state: dict[str, Any]
    external_state: dict[str, Any]
    affected_scope: tuple[str, ...]
    excluded_external: tuple[str, ...] = ()
    key_available: bool = True
    corrupt: bool = False


def _app_mapping(path: Path) -> dict[str, Any] | None:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mappings = [value for value in document.values() if isinstance(value, dict) and "recovery" in value]
    if not mappings:
        return None
    if len(mappings) != 1:
        raise AssertionError(f"{path}: expected one application defaults mapping")
    return mappings[0]


def _image_version(config: dict[str, Any]) -> str:
    app = config.get("app") if isinstance(config.get("app"), dict) else {}
    image = app.get("image")
    if isinstance(image, str) and image:
        # Keep evidence to a version label, not a rendered config value or an endpoint.
        return image.rsplit(":", 1)[-1] if ":" in image else image
    release = app.get("release")
    if isinstance(release, str) and release:
        return release
    return "declared-without-image"


def _native_case(slug: str, config: dict[str, Any], source_file: Path) -> MethodCase:
    recovery = config["recovery"]
    native = recovery.get("native", {})
    if not isinstance(native, dict) or not native.get("backup_playbook") or not native.get("restore_playbook"):
        raise AssertionError(f"{source_file}: native recovery is missing its backup/restore interface")
    role = ROOT / "ansible" / "roles" / slug
    if not (role / "tasks" / "restore.yml").is_file():
        raise AssertionError(f"{slug}: declared native recovery has no role task restore.yml")
    # Kubernetes-native backup capture may be expressed as a role task or as the
    # CronJob template that the role applies. Both are real adapter seams; do not
    # require every product to duplicate the shared backup-app dispatcher.
    backup_seams = ((role / "tasks" / "backup.yml"), (role / "templates" / "backup-cronjob.yaml.j2"))
    if not any(path.is_file() for path in backup_seams):
        raise AssertionError(f"{slug}: declared native recovery has no backup task or CronJob template")
    return MethodCase(
        product=slug,
        method="native",
        kind="application",
        version=_image_version(config),
        artifact_prefix=f"fixture/native/{slug}",
        assertions=APPLICATION_ASSERTIONS.get(slug, ("records", "files", "identities", "connections")),
        source_file=str(source_file.relative_to(ROOT)),
    )


def load_method_cases() -> list[MethodCase]:
    """Read declared product capabilities and add the shared PBS VM/LXC cases."""
    cases = [
        MethodCase(
            product="pbs-guest",
            method="pbs_guest",
            kind=kind,
            version="native-pbs-artifact",
            artifact_prefix=f"backup/{kind}/fixture-{kind}",
            assertions=("records", "files", "guest-identity", "target-connections"),
            source_file="ansible/playbooks/maintenance/restore-guest.yml",
            issue_reference=89,
            affected_scope=("fixture-guest", "fixture-workload-a", "fixture-workload-b"),
        )
        for kind in ("vm", "lxc")
    ]

    for source_file in sorted(DEFAULTS.glob("*.yml")):
        config = _app_mapping(source_file)
        if config is None:
            continue
        recovery = config["recovery"]
        methods = recovery.get("methods", [])
        if not isinstance(methods, list) or not methods:
            continue
        unknown = sorted(set(methods) - set(SUPPORTED_METHODS))
        if unknown:
            raise AssertionError(f"{source_file}: unsupported recovery methods {unknown}")
        slug = source_file.stem
        for method in methods:
            if method == "native":
                cases.append(_native_case(slug, config, source_file))
            elif method == "project_managed":
                capability = recovery.get(method, {})
                if not isinstance(capability, dict) or not capability.get("restore_role_task"):
                    raise AssertionError(f"{source_file}: project_managed recovery is missing restore_role_task")
                cases.append(MethodCase(
                    product=slug,
                    method=method,
                    kind="application",
                    version=_image_version(config),
                    artifact_prefix=f"fixture/project-managed/{slug}",
                    assertions=APPLICATION_ASSERTIONS.get(slug, ("records", "files", "identities", "connections")),
                    source_file=str(source_file.relative_to(ROOT)),
                ))
            elif method == "pbs_guest":
                capability = recovery.get(method, {})
                if not isinstance(capability, dict) or not capability.get("applicable", False):
                    raise AssertionError(f"{source_file}: pbs_guest recovery must declare applicable: true")
                cases.extend(
                    replace(case, product=slug, source_file=str(source_file.relative_to(ROOT)))
                    for case in cases[:2]
                )
    return cases


def catalog_remaining(cases: list[MethodCase] | None = None) -> list[str]:
    cases = cases if cases is not None else load_method_cases()
    covered = {case.product for case in cases if case.product != "pbs-guest"}
    document = yaml.safe_load(CATALOG.read_text(encoding="utf-8")) or {}
    applications = document.get("applications", {})
    return sorted(set(applications) - covered)


def new_fixture(case: MethodCase, name: str, marker: str = "A", *, kind: str | None = None) -> Fixture:
    fixture_kind = kind or case.kind
    return Fixture(
        name=name,
        kind=fixture_kind,
        method=case.method,
        version=case.version,
        storage_owner=name,
        route=f"{name}.isolated.invalid",
        identity=name,
        state={"records": {"primary": f"{name}-{marker}-record"}, "files": {"marker": f"{marker}.txt"}},
        external_state={"external-library": f"{name}-{marker}-external"},
        connections={"database": f"{name}-db", "route": f"{name}.isolated.invalid"},
        affected_scope=case.affected_scope,
    )


def native_artifact_id(case: MethodCase, label: str) -> str:
    """Return the provider artifact identity, preserving PBS's native path."""
    if case.method == "pbs_guest":
        numeric = "501" if case.kind == "vm" else "502"
        prefix = "vzdump-qemu" if case.kind == "vm" else "vzdump-lxc"
        return f"backup/{case.kind}/{numeric}/{prefix}-{numeric}-{label}"
    return f"{case.artifact_prefix}/{label}"


def artifact_identity(case: MethodCase, label: str) -> str:
    """Return a product-qualified fixture locator for pasted evidence."""
    native = native_artifact_id(case, label)
    if case.method == "pbs_guest":
        return f"fixture/pbs_guest/{case.product}/{native}"
    return native


def capture(case: MethodCase, fixture: Fixture, label: str, *, exclude_external: tuple[str, ...] = ()) -> RecoveryPoint:
    if fixture.method != case.method or fixture.kind != case.kind:
        raise ProtocolFailure("wrong target kind or method")
    artifact_id = native_artifact_id(case, label)
    return RecoveryPoint(
        artifact_id=artifact_id,
        method=fixture.method,
        kind=fixture.kind,
        source=fixture.name,
        version=fixture.version,
        state=deepcopy(fixture.state),
        external_state={key: value for key, value in fixture.external_state.items() if key not in exclude_external},
        affected_scope=fixture.affected_scope,
        excluded_external=exclude_external,
    )


def _preflight(
    case: MethodCase,
    target: Fixture,
    point: RecoveryPoint,
    *,
    expected_source: str,
    required_scope: tuple[str, ...] | None = None,
    require_external: bool = True,
) -> None:
    if point.source != expected_source:
        raise ProtocolFailure("wrong target recovery point")
    if point.method != case.method or point.kind != target.kind:
        raise ProtocolFailure("wrong target kind or method")
    if point.corrupt:
        raise ProtocolFailure("corrupt recovery artifact")
    if not point.key_available:
        raise ProtocolFailure("missing recovery key")
    if point.version != target.version:
        raise ProtocolFailure("incompatible recovery version")
    if target.storage_owner != target.name:
        raise ProtocolFailure("target storage maps to source-owned storage")
    if required_scope is not None and tuple(required_scope) != point.affected_scope:
        raise ProtocolFailure("shared guest affected scope is incomplete")
    if require_external and point.excluded_external:
        raise ProtocolFailure("required external data is excluded from the recovery point")


def restore_new(
    case: MethodCase,
    source: Fixture,
    target: Fixture,
    point: RecoveryPoint,
    *,
    required_scope: tuple[str, ...] | None = None,
    require_external: bool = True,
    failure_stage: str | None = None,
) -> dict[str, Any]:
    if target.name == source.name:
        raise ProtocolFailure("new destination aliases the source")
    if not target.isolated:
        raise ProtocolFailure("new destination is not isolated")
    if target.route == source.route:
        raise ProtocolFailure("new destination takes the source route")
    _preflight(
        case,
        target,
        point,
        expected_source=source.name,
        required_scope=required_scope,
        require_external=require_external,
    )
    target.active = False
    target.complete = False
    if failure_stage == "before_replace":
        raise ProtocolFailure("injected restore interruption before replacement")
    target.state = deepcopy(point.state)
    target.external_state = deepcopy(point.external_state)
    target.connections = {"database": f"{target.name}-db", "route": target.route}
    target.identity = target.name
    if failure_stage == "after_replace":
        raise ProtocolFailure("injected restore interruption after replacement")
    target.active = True
    target.complete = True
    return {"status": "passed", "identity": target.identity, "isolated": target.isolated}


def restore_existing(
    case: MethodCase,
    target: Fixture,
    point: RecoveryPoint,
    pre_restore_point: RecoveryPoint,
    *,
    failure_stage: str | None = None,
) -> dict[str, Any]:
    if target.name != point.source or target.name != pre_restore_point.source:
        raise ProtocolFailure("existing destination is not the exact target")
    if point.artifact_id == pre_restore_point.artifact_id:
        raise ProtocolFailure("existing destination has no independent pre-restore point")
    _preflight(case, target, point, expected_source=target.name, required_scope=target.affected_scope)
    _preflight(case, target, pre_restore_point, expected_source=target.name, required_scope=target.affected_scope)
    target.active = False
    target.complete = False
    if failure_stage == "after_quiesce":
        raise ProtocolFailure("injected restore interruption after quiesce")
    target.state = deepcopy(point.state)
    target.external_state = deepcopy(point.external_state)
    target.connections = {"database": f"{target.name}-db", "route": target.route}
    if failure_stage == "after_replace":
        raise ProtocolFailure("injected restore interruption after replacement")
    target.active = True
    target.complete = True
    return {"status": "passed", "identity": target.identity, "pre_restore_point": pre_restore_point.artifact_id}


def recover_preserved(target: Fixture, pre_restore_point: RecoveryPoint) -> None:
    if pre_restore_point.source != target.name:
        raise ProtocolFailure("preserved point belongs to a different target")
    target.state = deepcopy(pre_restore_point.state)
    target.external_state = deepcopy(pre_restore_point.external_state)
    target.connections = {"database": f"{target.name}-db", "route": target.route}
    target.active = True
    target.complete = True


def _expect_failure(function: Callable[[], Any], text: str) -> None:
    try:
        function()
    except ProtocolFailure as error:
        if text not in str(error):
            raise AssertionError(f"expected failure containing {text!r}, got {error!s}") from error
    else:
        raise AssertionError(f"expected recovery failure containing {text!r}")


def _assert_unchanged(before: Fixture, after: Fixture) -> None:
    for field_name in ("state", "external_state", "connections", "identity", "active", "complete", "production_effects"):
        if getattr(before, field_name) != getattr(after, field_name):
            raise AssertionError(f"failed preflight mutated {field_name}")


def run_negative_cases(case: MethodCase) -> list[str]:
    source = new_fixture(case, "fixture-source")
    point = capture(case, source, "A")
    results: list[str] = []

    def new_target() -> Fixture:
        target = new_fixture(case, "fixture-target", "EMPTY")
        target.active = False
        return target

    for name, bad_point, message in (
        ("missing-key", replace(point, key_available=False), "missing recovery key"),
        ("incompatible-version", replace(point, version="incompatible"), "incompatible recovery version"),
        ("corrupt-artifact", replace(point, corrupt=True), "corrupt recovery artifact"),
        ("excluded-external-data", capture(case, source, "A", exclude_external=("external-library",)), "required external data"),
    ):
        target = new_target()
        before = deepcopy(target)
        _expect_failure(lambda: restore_new(case, source, target, bad_point), message)
        _assert_unchanged(before, target)
        results.append(name)

    target = new_target()
    target.kind = "wrong-kind"
    before = deepcopy(target)
    _expect_failure(lambda: restore_new(case, source, target, point), "wrong target kind")
    _assert_unchanged(before, target)
    results.append("wrong-target")

    target = new_target()
    target.storage_owner = source.name
    before = deepcopy(target)
    _expect_failure(lambda: restore_new(case, source, target, point), "source-owned storage")
    _assert_unchanged(before, target)
    results.append("storage-boundary")

    target = new_target()
    target.route = source.route
    before = deepcopy(target)
    _expect_failure(lambda: restore_new(case, source, target, point), "source route")
    _assert_unchanged(before, target)
    results.append("identity-boundary")

    target = new_target()
    before = deepcopy(target)
    _expect_failure(
        lambda: restore_new(case, source, target, point, required_scope=("fixture-app",)),
        "affected scope",
    )
    _assert_unchanged(before, target)
    results.append("shared-guest-scope")

    # A partial restore must be observable as failed/inactive, never as a complete result.
    target = new_target()
    _expect_failure(lambda: restore_new(case, source, target, point, failure_stage="after_replace"), "after replacement")
    if target.complete or target.active:
        raise AssertionError("partial new restore reported a complete active target")
    results.append("partial-recovery")

    return results


def run_protocol(case: MethodCase) -> dict[str, Any]:
    """Run both destination routes and the required negative matrix for one method case."""
    source = new_fixture(case, "fixture-source")
    source_point = capture(case, source, "A")
    source_before = deepcopy(source)
    source.active = False
    source.reachable = False

    # A failed new restore is retryable without touching the source or an unrelated target.
    new_target = new_fixture(case, "fixture-new", "EMPTY")
    new_target.active = False
    corrupt = replace(source_point, corrupt=True)
    _expect_failure(lambda: restore_new(case, source, new_target, corrupt), "corrupt recovery artifact")
    if new_target.complete or new_target.active:
        raise AssertionError("failed new restore did not leave its target inactive")
    new_result = restore_new(case, source, new_target, source_point, required_scope=case.affected_scope)
    if new_target.state != source_point.state or new_target.identity != "fixture-new":
        raise AssertionError("new restore did not recover data with target identity")
    if new_target.connections != {"database": "fixture-new-db", "route": new_target.route}:
        raise AssertionError("new restore did not establish target-owned connections")
    if new_target.route == source.route or new_target.production_effects:
        raise AssertionError("new restore caused a production identity/side effect")
    if source.source_accesses or source.production_effects:
        raise AssertionError("new restore reached or changed the source")

    # Existing target: A -> B, retain B, restore A, then exercise interruption/retry and B recovery.
    existing = new_fixture(case, "fixture-existing", "A")
    existing_point_a = capture(case, existing, "A")
    existing.state = {"records": {"primary": "fixture-existing-B-record", "b-only": "present"}, "files": {"marker": "B.txt"}}
    existing.external_state = {"external-library": "fixture-existing-B-external"}
    existing_point_b = capture(case, existing, "B")
    existing_result = restore_existing(case, existing, existing_point_a, existing_point_b)
    if existing.state != existing_point_a.state or "b-only" in existing.state["records"]:
        raise AssertionError("existing restore did not replace B with A")
    if existing_point_b.artifact_id == existing_point_a.artifact_id:
        raise AssertionError("pre-restore B point was not independent")

    # Repeat from B, interrupt after replacement, recover to preserved B, then retry A.
    existing.state = deepcopy(existing_point_b.state)
    existing.external_state = deepcopy(existing_point_b.external_state)
    existing.active = True
    existing.complete = True
    _expect_failure(
        lambda: restore_existing(case, existing, existing_point_a, existing_point_b, failure_stage="after_replace"),
        "after replacement",
    )
    if existing.active or existing.complete:
        raise AssertionError("interrupted existing restore reported success")
    recover_preserved(existing, existing_point_b)
    if existing.state != existing_point_b.state or not existing.complete:
        raise AssertionError("preserved B point could not recover the failed target")
    restore_existing(case, existing, existing_point_a, existing_point_b)
    if existing.state != existing_point_a.state or not existing.complete or not existing.active:
        raise AssertionError("existing retry did not restore A")

    if source.state != source_before.state or source.production_effects:
        raise AssertionError("source changed during source-independent restore")
    negatives = run_negative_cases(case)
    return {
        "case": case.case_id,
        "new": {**new_result, "source_isolated": not source.reachable, "final_state": "A"},
        "existing": {**existing_result, "final_state": "A", "b_point_retained": True},
        "negative_cases": negatives,
        "evidence": evidence_record(case, source_point, existing_point_b, existing),
    }


def evidence_record(
    case: MethodCase,
    recovery_point: RecoveryPoint,
    pre_restore_point: RecoveryPoint,
    target: Fixture,
) -> dict[str, Any]:
    """Return the public evidence shape; no state payload or secret is included."""
    return {
        "instance": case.product,
        "method": case.method,
        "coverage": "fixture-tested",
        "state": "restore_tested",
        "version": case.version,
        "artifact_id": recovery_point.artifact_id,
        "artifact_identity": artifact_identity(case, "A"),
        "pre_restore_artifact_id": pre_restore_point.artifact_id,
        "pre_restore_artifact_identity": artifact_identity(case, "B"),
        "target_identity": target.identity,
        "target_state": "active-isolated" if target.active and target.isolated else "inactive",
        "connections_verified": True,
        "assertions": list(case.assertions),
        "source_file": case.source_file,
        "issue_reference": case.issue_reference,
        "live_verified": False,
    }


def build_rollup(
    cases: list[MethodCase] | None = None,
    *,
    fixture_tested: list[str] | None = None,
) -> dict[str, Any]:
    cases = cases if cases is not None else load_method_cases()
    tested = set(fixture_tested or ())
    records = [
        {
            "case": case.case_id,
            "product": case.product,
            "method": case.method,
            "kind": case.kind,
            "version": case.version,
            "version_source": case.source_file,
            "artifact_identity": artifact_identity(case, "A"),
            "assertions": list(case.assertions),
            "source": case.source_file,
            "issue_reference": case.issue_reference,
            "implemented": True,
            "fixture_tested": case.case_id in tested,
            "live_verified": False,
        }
        for case in cases
    ]
    return {
        "schema_version": 1,
        "acceptance_issue": 80,
        "ordering": "user-supplied; no priority is inferred from catalog or issue order",
        "implemented": records,
        "fixture_tested": sorted(tested),
        "live_verified": [],
        "catalog_remaining": catalog_remaining(cases),
    }


def assert_public_report(value: Any) -> None:
    """Reject values that would make a report unsafe to paste into an issue."""
    serialized = json.dumps(value, sort_keys=True).lower()
    if "secret" in serialized or "password" in serialized or "private key" in serialized:
        raise AssertionError("acceptance evidence contains a secret-shaped value")
    for match in re.finditer(r"(?:token|credential|key)\s*[:=]\s*[^,}]+", serialized):
        if "-rebind" not in match.group(0) and "encryption-key" not in match.group(0):
            raise AssertionError("acceptance evidence contains credential-shaped material")


def report_json() -> str:
    cases = load_method_cases()
    # The report command performs the protocol; it never labels a case fixture-tested
    # merely because a declaration exists.
    tested = [run_protocol(case)["case"] for case in cases]
    return json.dumps(build_rollup(cases, fixture_tested=tested), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(report_json(), end="")
