#!/usr/bin/env python3
"""Build a read-only product recovery coverage report.

The guest audit is intentionally a Proxmox view.  It cannot identify the application
inside a shared guest, enumerate a PVC's external backend, or prove that a native
artifact is usable.  This module joins that historical guest evidence with the tracked
catalog and product defaults without turning any of those inferences into a health
claim.  It is also usable without a live audit, which is useful when the runner or PVE
API is unavailable.

Only catalog metadata, capability declarations, and explicitly supplied evidence are
reported.  Credentials, raw guest configuration, and artifact contents are never read
or emitted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "catalog" / "applications.yml"
DEFAULTS = ROOT / "ansible" / "vars" / "app-defaults"
MANIFEST = ROOT / "catalog" / "recovery.yml"

SUPPORTED_METHODS = ("pbs_guest", "native", "project_managed")
AUDIT_STATES = {
    "missing",
    "configured_unverified",
    "stale",
    "verified_fresh",
    "restore_tested",
}
COMPONENT_STATES = {
    "unknown",
    "configured_unverified",
    "missing",
    "stale",
    "excluded",
    "incomplete",
    "verified",
    "failed",
}

# These products deliberately do not get a generic backup button.  Keep this list
# narrow: a product issue may replace a rebuild-only disposition with a real method,
# but a missing declaration must never be silently promoted to rebuild-only.
DOCUMENTED_REBUILD_ONLY = {
    "flaresolverr": "Stateless Kubernetes workload; redeploy reconstructs its state.",
    "homepage": "Generated dashboard data is reconstructed from platform topology.",
    "kometa": "Generated Kubernetes configuration is reconstructed from media wiring.",
    "searxng": (
        "Stateless Kubernetes configuration is rebuilt from tracked defaults and the "
        "canonical application credential; its in-namespace limiter cache is disposable."
    ),
    "unpackerr": "Stateless daemon configuration is reconstructed from the media registry.",
}


def _mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return value


def _defaults(path: Path) -> dict[str, Any]:
    document = _mapping(path)
    values = [
        value
        for key, value in document.items()
        if key.endswith("_defaults") and isinstance(value, dict)
    ]
    if len(values) != 1:
        raise ValueError(f"{path}: expected exactly one application defaults mapping")
    return values[0]


def load_inputs(
    *,
    catalog_path: Path = CATALOG,
    defaults_dir: Path = DEFAULTS,
    manifest_path: Path = MANIFEST,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Load and validate the tracked catalog, defaults, and recovery ownership map."""
    catalog_doc = _mapping(catalog_path)
    applications = catalog_doc.get("applications")
    if not isinstance(applications, dict):
        raise ValueError(f"{catalog_path}: applications must be a mapping")
    manifest = _mapping(manifest_path)
    if manifest.get("schema_version") != 1:
        raise ValueError(f"{manifest_path}: expected schema_version: 1")
    products = manifest.get("products")
    if not isinstance(products, dict):
        raise ValueError(f"{manifest_path}: products must be a mapping")
    catalog_slugs = set(applications)
    manifest_slugs = set(products)
    if catalog_slugs != manifest_slugs:
        missing = sorted(catalog_slugs - manifest_slugs)
        extra = sorted(manifest_slugs - catalog_slugs)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise ValueError(f"{manifest_path}: product map does not match catalog ({'; '.join(details)})")

    resolved: dict[str, dict[str, Any]] = {}
    for slug, entry in applications.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{catalog_path}: applications.{slug} must be a mapping")
        if not isinstance(products[slug], dict):
            raise ValueError(f"{manifest_path}: products.{slug} must be a mapping")
        defaults_path = defaults_dir / f"{slug}.yml"
        if not defaults_path.is_file():
            raise ValueError(f"{defaults_path}: catalog product has no defaults file")
        resolved[slug] = {
            "catalog": entry,
            "defaults": _defaults(defaults_path),
            "manifest": products[slug],
            "defaults_path": str(defaults_path),
        }
    return resolved, {str(k): v for k, v in products.items()}, manifest


def _issue(number: Any) -> dict[str, Any]:
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise ValueError("recovery issue references must be positive integers")
    return {
        "number": number,
        "url": f"https://github.com/hardKOrr/homelab-infra/issues/{number}",
    }


def _hosting(slug: str, defaults: dict[str, Any]) -> tuple[str, str]:
    stack = str(defaults.get("stack") or "")
    hosting = str(defaults.get("hosting") or ("docker" if stack else "native"))
    if hosting not in {"native", "docker", "kubernetes", "appliance"}:
        raise ValueError(f"{slug}: unsupported hosting kind {hosting!r}")
    return hosting, stack


def _guest_kind(defaults: dict[str, Any], hosting: str) -> str:
    proxmox = defaults.get("proxmox") if isinstance(defaults.get("proxmox"), dict) else {}
    if hosting == "appliance" or isinstance(proxmox.get("vm"), dict):
        return "VM"
    return "LXC"


def _version(defaults: dict[str, Any]) -> str:
    app = defaults.get("app") if isinstance(defaults.get("app"), dict) else {}
    image = app.get("image")
    if isinstance(image, str) and image:
        return image.rsplit(":", 1)[-1] if ":" in image else image
    release = app.get("release")
    if isinstance(release, str) and release:
        return release
    return "declared-without-image"


def _shared_guest_declared(defaults: dict[str, Any]) -> bool:
    """Return whether defaults explicitly mark the hosting guest as cross-estate shared."""
    proxmox = defaults.get("proxmox") if isinstance(defaults.get("proxmox"), dict) else {}
    candidates = [proxmox]
    for kind in ("lxc", "vm"):
        guest = proxmox.get(kind)
        if isinstance(guest, dict):
            candidates.append(guest)
    return any(
        isinstance(candidate.get("tags"), list) and "_.shared" in candidate["tags"]
        for candidate in candidates
    )


def _shared_effects(slug: str, resolved: dict[str, dict[str, Any]], hosting: str, stack: str) -> list[str]:
    if stack:
        siblings = sorted(
            name
            for name, item in resolved.items()
            if _hosting(name, item["defaults"])[1] == stack
        )
        return [f"shared stack guest {stack}: {', '.join(siblings)}"]
    if hosting == "kubernetes":
        siblings = sorted(
            name
            for name, item in resolved.items()
            if _hosting(name, item["defaults"])[0] == "kubernetes"
        )
        return ["shared Kubernetes node/cluster guest: " + ", ".join(siblings)]
    defaults = resolved[slug]["defaults"]
    if _shared_guest_declared(defaults):
        if slug == "caddy":
            return [
                "shared lab Caddy LXC: every catalog application's HTTPS route, TLS and access policy"
            ]
        return [f"shared lab hosting guest {slug}: all workloads on this hosting substrate"]
    return []


def _recovery_unit(slug: str, defaults: dict[str, Any], hosting: str, stack: str) -> str:
    if hosting == "kubernetes":
        if slug == "searxng":
            return (
                "the shared Kubernetes node/cluster guest and all workloads, including "
                "SearXNG's stateless namespace and disposable limiter cache; not an "
                "application-only restore"
            )
        return (
            "application PVCs and named external backends; the Kubernetes node/cluster "
            "guest is a shared PBS recovery unit, not an application-only restore"
        )
    if stack:
        return f"the shared {stack} {_guest_kind(defaults, hosting)} guest and its workloads"
    if slug == "caddy" and _shared_guest_declared(defaults):
        return "the shared Caddy LXC guest and every routed application's edge connection"
    return f"the {slug} {_guest_kind(defaults, hosting)} guest"


def _native_unit(slug: str, defaults: dict[str, Any], hosting: str, stack: str) -> str:
    if slug == "searxng":
        return (
            "stateless SearXNG configuration and disposable in-namespace limiter cache "
            "on the shared Kubernetes cluster"
        )
    if slug == "plane":
        return (
            "Plane server/worker state, named PostgreSQL database, named Redis dataset and "
            "local MinIO object-storage path in one application-consistent PBS point"
        )
    app = defaults.get("app") if isinstance(defaults.get("app"), dict) else {}
    database = app.get("database")
    if isinstance(database, dict) and database.get("provider") and database.get("instance"):
        return f"{slug} application state plus named {database['provider']} backend {database['instance']}"
    if isinstance(database, dict):
        return f"{slug} application state plus its owned database volume"
    redis = app.get("redis")
    if isinstance(redis, dict) and redis.get("instance"):
        return f"{slug} application state plus its named Redis dependency"
    if isinstance(redis, dict):
        return f"{slug} application state plus its owned cache volume"
    if "media_storage" in defaults:
        return f"{slug} application state plus its named media-storage mounts"
    if "storage" in app or "config_path" in app or "data_path" in app:
        return f"{slug} application-owned persistent storage and configuration"
    if hosting == "kubernetes":
        return f"{slug} application objects and persistent volumes on the shared cluster"
    return f"{slug} application-owned state on the {stack or 'application'} guest"


def _external_requirements(
    slug: str,
    defaults: dict[str, Any],
    supplied: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    app = defaults.get("app") if isinstance(defaults.get("app"), dict) else {}
    result: list[dict[str, Any]] = []

    def add(kind: str, description: str, *, required: bool = True) -> None:
        result.append({
            "kind": kind,
            "description": description,
            "required": required,
            "status": "unknown",
            "disposition": "independent-evidence-required",
        })

    database = app.get("database")
    if isinstance(database, dict) and database.get("provider") and database.get("instance"):
        add(
            "database",
            f"named {database['provider']} backend {database['instance']}",
        )
    redis = app.get("redis")
    if isinstance(redis, dict) and redis.get("instance"):
        add("cache", f"named Redis dependency {redis['instance']}", required=slug == "plane")
    if "media_storage" in defaults:
        add("mount", "operator-declared media/storage mount; guest audit cannot inspect its contents")
    if app.get("recordings_path"):
        add("mount", "continuous-write recordings path; storage-owner backup is separate from the guest snapshot")
    if "storage" in app:
        add("persistent-volume", "application PVC/storage volume and its node/persistence policy")
    if app.get("object_storage") and isinstance(app["object_storage"], dict):
        obj = app["object_storage"]
        if obj.get("instance"):
            add("object-storage", f"named object-storage service {obj['instance']} and bucket")
    if isinstance(app.get("rabbitmq"), dict):
        add("message-bus", "named or externally reachable RabbitMQ dependency")
    if isinstance(app.get("forgejo"), dict):
        add("upstream", "named Forgejo service and registration identity")
    if isinstance(app.get("upstreams"), dict):
        add("upstream", "operator-selected inference upstream(s)")
    if isinstance(app.get("mqtt"), dict) and app["mqtt"].get("host"):
        add("upstream", "configured MQTT broker and camera state")
    if isinstance(app.get("cameras"), dict) and app["cameras"]:
        add("external-service", "camera endpoints and their independently stored credentials")
    for key, label in (
        ("shared_device", "shared physical device"),
        ("usb_mapping", "USB/Zigbee device mapping"),
        ("pci_device", "dedicated PCI device"),
        ("igpu_render_node", "shared iGPU render device"),
        ("coral_usb_mapping", "Coral USB device mapping"),
    ):
        if key in app:
            add("hardware", label, required=False)
    if not result:
        add("external-data", "no product-specific external data was declared; live guest mounts remain undiscovered", required=False)
    if isinstance(supplied, dict):
        common = supplied if "status" in supplied or "state" in supplied else None
        for item in result:
            evidence = common or supplied.get(item["kind"])
            if isinstance(evidence, dict):
                reduced = _component(evidence)
                item["status"] = reduced["status"]
                for key in ("reason", "notes"):
                    if key in reduced:
                        item[key] = reduced[key]
    return result


def _credential_disposition(
    slug: str,
    defaults: dict[str, Any],
    rebuild_only: bool,
    supplied: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "required": not rebuild_only,
        "status": "unknown",
        "disposition": "independent-vaultwarden-evidence-required" if not rebuild_only else "not-required-for-rebuild",
        "scope": "application credentials, encryption keys, identities and permissions; names and values are intentionally omitted",
    }
    if isinstance(supplied, dict):
        evidence = _component(supplied)
        result["status"] = evidence["status"]
        for key in ("reason", "notes"):
            if key in evidence:
                result[key] = evidence[key]
    return result


def _component(value: Any, *, default: str = "unknown") -> dict[str, Any]:
    """Reduce supplied evidence to the safe, shared status vocabulary."""
    if not isinstance(value, dict):
        return {"status": default}
    raw = value.get("status", value.get("state"))
    if value.get("unreadable") or raw == "unreadable":
        status = "unknown"
        reason = "evidence-unreadable"
    elif raw in AUDIT_STATES:
        status = {
            "verified_fresh": "verified",
            "restore_tested": "verified",
            "configured_unverified": "configured_unverified",
            "missing": "missing",
            "stale": "stale",
        }[raw]
        reason = None
    elif raw in COMPONENT_STATES:
        status = raw
        reason = None
    else:
        status = default
        reason = None
    result: dict[str, Any] = {"status": status}
    if reason:
        result["reason"] = reason
    for key in ("artifact_id", "captured_at", "restore_tested_at", "age_hours", "notes", "reason"):
        if key in value and value[key] is not None:
            result[key] = value[key]
    return result


def _evidence(
    supplied: dict[str, Any] | None = None,
    *,
    schedule: str = "unknown",
    artifact: dict[str, Any] | None = None,
    external: str = "unknown",
) -> dict[str, Any]:
    supplied = supplied if isinstance(supplied, dict) else {}
    result = {
        "schedule": {"status": schedule},
        "artifact": _component(artifact),
        "integrity": _component(supplied.get("integrity")),
        "external_data": _component(supplied.get("external_data"), default=external),
        "restore": _component(supplied.get("restore")),
    }
    if supplied.get("artifact") is not None:
        result["artifact"] = _component(supplied["artifact"])
    return result


def _guest_rows_for_product(slug: str, audit: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(audit, dict):
        return []
    explicit = audit.get("product_guests", {})
    names = explicit.get(slug, []) if isinstance(explicit, dict) else []
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        return []
    rows = audit.get("guests", [])
    if not isinstance(rows, list):
        return []
    wanted = {str(value) for value in names}
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and (str(row.get("name")) in wanted or str(row.get("vmid")) in wanted)
    ]


def _pbs_evidence(slug: str, audit: dict[str, Any] | None) -> dict[str, Any]:
    rows = _guest_rows_for_product(slug, audit)
    if len(rows) != 1:
        reason = "application identity is not established by the guest audit"
        if len(rows) > 1:
            reason = "multiple explicit guest mappings require operator resolution"
        return {
            "schedule": {"status": "unknown"},
            "artifact": {"status": "unknown", "reason": reason},
            "integrity": {"status": "unknown"},
            "external_data": {"status": "unknown", "reason": "guest audit does not prove external-data protection"},
            "restore": {"status": "unknown"},
        }
    row = rows[0]
    artifact = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    artifact = dict(artifact)
    artifact["artifact_id"] = row.get("artifact_id")
    artifact["captured_at"] = row.get("captured_at")
    artifact["restore_tested_at"] = row.get("restore_tested_at")
    artifact["age_hours"] = (row.get("latest_candidate") or {}).get("age_hours")
    # PR #81 maps unreadable collection to a deliberately non-healthy evidence state.
    # Do not let the nested shared record's configured_unverified fallback hide that fact.
    if row.get("guest_snapshot") == "unknown":
        artifact = {"status": "unknown", "reason": "guest audit evidence was unreadable"}
    external_status = "excluded" if row.get("external_or_excluded_data") else "unknown"
    return {
        "schedule": {"status": row.get("schedule", "unknown")},
        "artifact": _component(artifact),
        "integrity": _component({"status": row.get("artifact_integrity", "unknown")}),
        "external_data": {
            "status": external_status,
            "reason": "guest exclusions are outside PBS coverage" if external_status == "excluded" else "guest audit does not prove external-data protection",
        },
        "restore": _component({"status": row.get("restore_test", "unknown")}),
    }


def _native_evidence(slug: str, defaults: dict[str, Any], supplied: dict[str, Any] | None) -> dict[str, Any]:
    backup = defaults.get("backup") if isinstance(defaults.get("backup"), dict) else {}
    schedule = "configured" if backup.get("enabled") and backup.get("schedule") else "missing"
    product_supplied = supplied.get(slug, {}) if isinstance(supplied, dict) else {}
    return _evidence(product_supplied, schedule=schedule)


def _method(
    slug: str,
    method: str,
    defaults: dict[str, Any],
    hosting: str,
    stack: str,
    resolved: dict[str, dict[str, Any]],
    *,
    audit: dict[str, Any] | None,
    native_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    recovery = defaults.get("recovery") if isinstance(defaults.get("recovery"), dict) else {}
    declared = method in (recovery.get("methods") or [])
    if method == "pbs_guest":
        return {
            "declared": True,
            "applicable": True,
            "availability": "shared_guest_only" if hosting == "kubernetes" else "applicable",
            "recovery_unit": _recovery_unit(slug, defaults, hosting, stack),
            "shared_guest_effects": _shared_effects(slug, resolved, hosting, stack),
            "evidence": _pbs_evidence(slug, audit),
            "limitations": [
                "guest audit cannot establish application identity inside a shared guest",
                "guest-mounted external storage and application-specific artifacts require separate evidence",
            ],
        }
    if declared:
        capability = recovery.get(method) if isinstance(recovery.get(method), dict) else {}
        return {
            "declared": True,
            "applicable": True,
            "availability": "declared",
            "recovery_unit": _native_unit(slug, defaults, hosting, stack),
            "interface": {key: capability.get(key) for key in ("backup_playbook", "restore_playbook", "restore_role_task") if capability.get(key)},
            "evidence": _native_evidence(slug, defaults, native_evidence),
            "limitations": [
                "configured schedule is not a successful artifact",
                "artifact integrity and restore evidence require a product acceptance run",
            ],
        }
    return {
        "declared": False,
        "applicable": False,
        "availability": "not_declared",
        "recovery_unit": _native_unit(slug, defaults, hosting, stack),
        "evidence": {
            "schedule": {"status": "unknown"},
            "artifact": {"status": "unknown", "reason": "method is not declared"},
            "integrity": {"status": "unknown"},
            "external_data": {"status": "unknown"},
            "restore": {"status": "unknown"},
        },
    }


def _product(
    slug: str,
    item: dict[str, Any],
    resolved: dict[str, dict[str, Any]],
    *,
    audit: dict[str, Any] | None,
    native_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    defaults = item["defaults"]
    catalog = item["catalog"]
    manifest = item["manifest"]
    hosting, stack = _hosting(slug, defaults)
    recovery = defaults.get("recovery") if isinstance(defaults.get("recovery"), dict) else {}
    declared_methods = recovery.get("methods") or []
    unknown = sorted(set(declared_methods) - set(SUPPORTED_METHODS))
    if unknown:
        raise ValueError(f"{slug}: unsupported recovery methods {unknown}")
    if manifest.get("rebuild_only") is True:
        conflicting = sorted(set(declared_methods) & {"native", "project_managed"})
        if conflicting:
            raise ValueError(
                f"{slug}: rebuild_only cannot coexist with declared recovery methods {conflicting}"
            )
    product_evidence = native_evidence.get(slug, {}) if isinstance(native_evidence, dict) else {}
    methods = {
        method: _method(
            slug,
            method,
            defaults,
            hosting,
            stack,
            resolved,
            audit=audit,
            native_evidence=native_evidence,
        )
        for method in SUPPORTED_METHODS
    }
    rebuild_only = manifest.get("rebuild_only")
    if rebuild_only is True:
        rebuild_only = DOCUMENTED_REBUILD_ONLY.get(slug, "Rebuild-only behavior is documented by the product issue.")
    if not rebuild_only and "native" in declared_methods:
        fallback = {"disposition": "native-declared", "reason": "native method is the selected product path; no fallback is claimed"}
    elif "project_managed" in declared_methods:
        fallback = {"disposition": "project-managed-declared", "reason": "project-managed method is declared by product defaults"}
    elif rebuild_only:
        fallback = {"disposition": "rebuild-only", "reason": str(rebuild_only)}
    else:
        fallback = {"disposition": "pbs-guest-only", "reason": "PBS can recover the declared guest unit, but no application-native or project-managed fallback is declared"}
    issue = _issue(manifest.get("recovery_issue"))
    row = {
        "product": slug,
        "name": catalog.get("name", slug),
        "version": _version(defaults),
        "hosting": hosting,
        "scope": catalog.get("scope"),
        "recovery_issue": issue,
        "deployment_issue": _issue(manifest["deployment_issue"]) if manifest.get("deployment_issue") else None,
        "priority": {
            "selection": "pending_user_selection",
            "source": "user must-keep selection, not catalog or issue order",
        },
        "available_methods": ["pbs_guest"] + list(declared_methods),
        "methods": methods,
        "fallback": fallback,
        "external_data": _external_requirements(slug, defaults, product_evidence.get("external_data")),
        "credentials": _credential_disposition(slug, defaults, bool(rebuild_only), product_evidence.get("credentials")),
        "shared_guest_effects": _shared_effects(slug, resolved, hosting, stack),
        "discovery_limitations": [
            "guest audit cannot discover application identity, native schedules, databases, secrets or guest-mounted remote filesystems",
            "bind/device mounts and excluded guest disks remain outside a PBS guest artifact unless independently covered",
        ],
    }
    return row


def build_report(
    *,
    audit: dict[str, Any] | None = None,
    native_evidence: dict[str, Any] | None = None,
    catalog_path: Path = CATALOG,
    defaults_dir: Path = DEFAULTS,
    manifest_path: Path = MANIFEST,
) -> dict[str, Any]:
    """Build the non-secret coverage inventory and optionally join guest evidence."""
    resolved, _, manifest = load_inputs(
        catalog_path=catalog_path,
        defaults_dir=defaults_dir,
        manifest_path=manifest_path,
    )
    products = [
        _product(slug, resolved[slug], resolved, audit=audit, native_evidence=native_evidence)
        for slug in sorted(resolved)
    ]
    legacy = []
    for source in manifest.get("legacy_sources", []) or []:
        if not isinstance(source, dict) or not source.get("id") or not source.get("product"):
            raise ValueError(f"{manifest_path}: every legacy source needs id and product")
        legacy.append({
            "id": source["id"],
            "product": source["product"],
            "source": source.get("source", "unidentified legacy source"),
            "recovery_issue": _issue(source["recovery_issue"]),
            "deployment_issue": _issue(source["deployment_issue"]) if source.get("deployment_issue") else None,
            "priority": "pending_user_selection",
            "disposition": source.get("disposition", "preserve source; do not adopt or mutate"),
        })
    report: dict[str, Any] = {
        "schema_version": 1,
        "source": "tracked catalog, product defaults, recovery ownership map, and optional explicit evidence",
        "priority": {
            "selection": "pending_user_selection",
            "source": "user-selected must-keep instances are independent of discovery and issue order",
        },
        "limitations": [
            "PR #81 guest evidence is preserved under guest_audit and remains guest-scoped",
            "no product identity is inferred from a guest name, VMID, stack, or snapshot shape",
            "unknown, missing, stale, excluded, incomplete, and unreadable evidence is never promoted to healthy",
            "live restore, artifact integrity, external-data protection, and credentials remain unverified until separately recorded",
        ],
        "products": products,
        "legacy_sources": legacy,
    }
    if isinstance(audit, dict):
        # Keep the historical PR #81 document intact, but only carry its safe, structured
        # fields into the combined report. The collector already suppresses raw configs;
        # this allowlist prevents a future caller from accidentally widening that boundary.
        report["guest_audit"] = {
            key: audit[key]
            for key in (
                "schema_version",
                "observed_at",
                "storage_view_node",
                "max_age_hours",
                "read_errors",
                "scope",
                "limitations",
                "evidence_schema_version",
                "evidence",
                "guests",
            )
            if key in audit
        }
    return report


def _read_json(path: str) -> dict[str, Any]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-json", help="PR #81 audit JSON, or '-' for stdin")
    parser.add_argument("--native-evidence-json", help="optional explicit product evidence JSON")
    args = parser.parse_args()
    try:
        report = build_report(
            audit=_read_json(args.audit_json) if args.audit_json else None,
            native_evidence=_read_json(args.native_evidence_json) if args.native_evidence_json else None,
        )
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as error:
        parser.exit(1, f"coverage: {error}\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
