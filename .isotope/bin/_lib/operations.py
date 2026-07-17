"""Operating lifecycle state machines: `run status`, `run arm`, `run teardown`.

Arm binds exactly one flux specimen to a branch, resolved base commit, and
specimen revision tail over a clean Git-visible baseline. Teardown validates
identity and removes only the operating binding — branch, worktree, and
specimen history are preserved. Both are idempotent across every crash window
documented in docs/isotope/RECOVERY.md.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import gitops, specimens
from .errors import EXIT_CONFLICT, EXIT_REFUSED, IsotopeError
from .journal import JournalWrite, run_transaction, transaction_scope
from .operating import OPERATING_RELATIVE, read_operating
from .paths import Project
from .revisions import revision
from .schemas import validate as validate_schema


def status(project: Project) -> dict[str, Any]:
    """Read-only armed-state diagnosis; repeated calls against unchanged state agree."""
    operating = read_operating(project)
    if operating is None:
        return {"armed": False, "operating": None, "checks": None}
    checks: dict[str, Any] = {}
    try:
        located = specimens.locate(project, operating["slug"])
        _, current = specimens.read_validated(located)
        if located.stage not in ("flux", "stable"):
            checks["specimen"] = f"wrong-stage:{located.stage}"
        elif current != operating["specimen_revision"]:
            checks["specimen"] = "revision-mismatch"
        else:
            checks["specimen"] = "ok"
    except IsotopeError as exc:
        checks["specimen"] = exc.code
    head_branch = gitops.current_branch(project)
    expected_branch = operating.get("target_branch") if operating["state"] == "landed" else operating["branch"]
    checks["branch"] = "ok" if head_branch == expected_branch else f"head-on:{head_branch}"
    checks["baseline"] = gitops.baseline_violations(project)
    return {"armed": operating["state"] == "armed", "operating": operating, "checks": checks}


def arm(project: Project, slug: str, branch: str, base_ref: str) -> dict[str, Any]:
    with transaction_scope(project):
        return _arm_locked(project, slug, branch, base_ref)


def _arm_locked(project: Project, slug: str, branch: str, base_ref: str) -> dict[str, Any]:
    # Validate the caller's current specimen before any Git effect, then repeat
    # the identity check after switching because tracked lifecycle state may
    # differ between branches.
    located = specimens.locate(project, slug)
    if located.stage != "flux":
        raise IsotopeError(
            "wrong-stage",
            "Only a flux specimen can be armed.",
            EXIT_REFUSED,
            {"slug": slug, "stage": located.stage},
        )
    _, current = specimens.read_validated(located)
    base_commit = gitops.resolve_commit(project, base_ref)
    existing = read_operating(project)
    if existing is not None:
        identical = (
            existing["slug"] == slug
            and existing["branch"] == branch
            and existing["base_commit"] == base_commit
            and existing["specimen_revision"] == current
        )
        if identical and existing["state"] == "armed":
            return existing  # idempotent re-arm
        raise IsotopeError(
            "already-armed",
            "A different operation is already armed; tear it down first.",
            EXIT_CONFLICT,
            {"armed": existing["slug"], "requested": slug},
        )
    violations = gitops.baseline_violations(project)
    if violations:
        raise IsotopeError(
            "dirty-baseline",
            "Arm requires a clean Git-visible baseline outside Isotope-managed state.",
            EXIT_REFUSED,
            {"paths": violations},
        )
    # Git effects ride before the journal: they are idempotent re-checks on retry
    # and leave no Isotope state behind if the process dies here.
    if not gitops.branch_exists(project, branch):
        gitops.create_branch(project, branch, base_commit)
    if gitops.current_branch(project) != branch:
        gitops.switch_branch(project, branch)
    if gitops.current_branch(project) != branch:
        raise IsotopeError(
            "git-failed",
            "HEAD is not on the requested branch after switching.",
            EXIT_CONFLICT,
            {"branch": branch},
        )
    violations = gitops.baseline_violations(project)
    if violations:
        raise IsotopeError(
            "dirty-baseline",
            "Arm requires a clean Git-visible baseline outside Isotope-managed state.",
            EXIT_REFUSED,
            {"paths": violations},
        )
    located = specimens.locate(project, slug)
    if located.stage != "flux":
        raise IsotopeError(
            "wrong-stage",
            "Only a flux specimen can be armed.",
            EXIT_REFUSED,
            {"slug": slug, "stage": located.stage},
        )
    _, current = specimens.read_validated(located)
    operating = {
        "schema_version": "1",
        "slug": slug,
        "branch": branch,
        "base_commit": base_commit,
        "specimen_revision": current,
        "state": "armed",
    }
    run_transaction(
        project,
        journal_type="operating",
        operation="run.arm",
        writes=[JournalWrite(OPERATING_RELATIVE, None, revision(operating), operating)],
    )
    return operating


def teardown(project: Project, slug: str) -> dict[str, Any]:
    with transaction_scope(project):
        return _teardown_locked(project, slug)


def _teardown_locked(project: Project, slug: str) -> dict[str, Any]:
    operating = read_operating(project)
    if operating is None:
        raise IsotopeError(
            "no-armed-operation",
            "No operation is armed.",
            EXIT_REFUSED,
            {"slug": slug},
        )
    if operating["slug"] != slug:
        raise IsotopeError(
            "operation-mismatch",
            "The armed operation drives a different specimen.",
            EXIT_REFUSED,
            {"slug": slug, "armed": operating["slug"]},
        )
    if operating["state"] in ("landing", "landed"):
        raise IsotopeError(
            "lifecycle-refused",
            "A deployment in progress must finish through deploy or cleanup.",
            EXIT_REFUSED,
            {"slug": slug, "state": operating["state"]},
        )
    located = specimens.locate(project, slug)
    _, current = specimens.read_validated(located)
    if current != operating["specimen_revision"]:
        raise IsotopeError(
            "chain-broken",
            "The armed operation's recorded revision does not match the specimen; "
            "teardown refuses rather than discard evidence.",
            EXIT_CONFLICT,
            {"recorded": operating["specimen_revision"], "actual": current},
        )
    run_transaction(
        project,
        journal_type="operating",
        operation="run.teardown",
        writes=[JournalWrite(OPERATING_RELATIVE, revision(operating), None)],
    )
    return {"slug": slug, "branch": operating["branch"], "state": "torn-down"}


def _replace_operating(project: Project, prior: dict[str, Any], updated: dict[str, Any], operation: str) -> dict[str, Any]:
    validate_schema("operating", updated)
    run_transaction(
        project,
        journal_type="operating",
        operation=operation,
        writes=[JournalWrite(OPERATING_RELATIVE, revision(prior), revision(updated), updated)],
    )
    return updated


def park(project: Project, slug: str) -> dict[str, Any]:
    with transaction_scope(project):
        operating = read_operating(project)
        if operating is None or operating["slug"] != slug:
            raise IsotopeError("no-armed-operation", "The requested operation is not armed.", EXIT_REFUSED, {"slug": slug})
        if operating["state"] == "parked":
            return operating
        if operating["state"] != "armed":
            raise IsotopeError("lifecycle-refused", "Only an armed operation can be parked.", EXIT_REFUSED, {"state": operating["state"]})
        located = specimens.locate(project, slug)
        _, current = specimens.read_validated(located)
        if located.stage != "flux" or current != operating["specimen_revision"]:
            raise IsotopeError("chain-broken", "Park requires the bound flux specimen revision.", EXIT_CONFLICT)
        if gitops.current_branch(project) != operating["branch"]:
            raise IsotopeError("branch-mismatch", "Park requires the operation branch to be current.", EXIT_CONFLICT, {"branch": operating["branch"]})
        updated = {**operating, "state": "parked", "parked_head": gitops.head_commit(project)}
        return _replace_operating(project, operating, updated, "run.park")


def resume(project: Project, slug: str) -> dict[str, Any]:
    with transaction_scope(project):
        operating = read_operating(project)
        if operating is None or operating["slug"] != slug:
            raise IsotopeError("no-parked-operation", "The requested operation is not parked.", EXIT_REFUSED, {"slug": slug})
        if operating["state"] == "armed":
            return operating
        if operating["state"] != "parked":
            raise IsotopeError("lifecycle-refused", "Only a parked operation can be resumed.", EXIT_REFUSED, {"state": operating["state"]})
        if gitops.current_branch(project) != operating["branch"] or gitops.head_commit(project) != operating["parked_head"]:
            raise IsotopeError("parked-source-moved", "Resume requires the parked branch and HEAD to remain unchanged.", EXIT_CONFLICT, {"branch": operating["branch"], "head": operating["parked_head"]})
        located = specimens.locate(project, slug)
        _, current = specimens.read_validated(located)
        if located.stage != "flux" or current != operating["specimen_revision"]:
            raise IsotopeError("chain-broken", "Resume requires the parked specimen revision.", EXIT_CONFLICT)
        updated = dict(operating)
        updated["state"] = "armed"
        updated.pop("parked_head", None)
        return _replace_operating(project, operating, updated, "run.resume")


def _landing_state(project: Project, operating: dict[str, Any], *, target: str, operation_head: str, landed_commit: str, reason: str) -> dict[str, Any]:
    updated = {
        **operating,
        "state": "landing",
        "target_branch": target,
        "operation_head": operation_head,
        "landed_commit": landed_commit,
        "landing_reason": reason,
    }
    with transaction_scope(project):
        current = read_operating(project)
        if current != operating:
            raise IsotopeError("operating-moved", "The operating state changed before landing.", EXIT_CONFLICT)
        return _replace_operating(project, current, updated, "run.deploy.landing")


def _mark_landed(project: Project, operating: dict[str, Any]) -> dict[str, Any]:
    updated = {**operating, "state": "landed"}
    with transaction_scope(project):
        current = read_operating(project)
        if current is None:
            return updated
        if current["state"] == "landed":
            return current
        return _replace_operating(project, current, updated, "run.deploy.landed")


def cleanup(project: Project, slug: str) -> dict[str, Any]:
    operating = read_operating(project)
    if operating is None:
        located = specimens.locate(project, slug)
        if located.stage == "stable":
            return {"slug": slug, "state": "deployed", "target_branch": gitops.current_branch(project)}
        raise IsotopeError("no-landed-operation", "No landed operation is available for cleanup.", EXIT_REFUSED, {"slug": slug})
    if operating["slug"] != slug or operating["state"] != "landed":
        raise IsotopeError("lifecycle-refused", "Cleanup requires the matching landed operation.", EXIT_REFUSED, {"slug": slug, "state": operating["state"]})
    target = operating["target_branch"]
    landed_commit = operating["landed_commit"]
    operation_branch = operating["branch"]
    operation_head = operating["operation_head"]
    branch = gitops.current_branch(project)
    if branch == operation_branch:
        if gitops.index_paths(project) or gitops.baseline_violations(project):
            raise IsotopeError("dirty-cleanup", "Cleanup requires a clean landed worktree.", EXIT_REFUSED, {"staged": gitops.index_paths(project), "changed": gitops.baseline_violations(project)})
        gitops.switch_branch(project, target)
    elif branch != target:
        raise IsotopeError("branch-mismatch", "Cleanup requires the operation or target branch.", EXIT_CONFLICT, {"operation_branch": operation_branch, "target_branch": target, "actual": branch})
    if gitops.head_commit(project) != landed_commit:
        raise IsotopeError("target-moved", "The target branch no longer names the landed commit.", EXIT_CONFLICT, {"target_branch": target})
    gitops.delete_branch(project, operation_branch, operation_head)
    with transaction_scope(project):
        current = read_operating(project)
        if current is not None:
            run_transaction(
                project,
                journal_type="operating",
                operation="run.cleanup",
                writes=[JournalWrite(OPERATING_RELATIVE, revision(current), None)],
            )
    return {"slug": slug, "state": "deployed", "target_branch": target, "landed_commit": landed_commit}


def deploy(project: Project, slug: str, payload: Any) -> dict[str, Any]:
    validate_schema("deploy-input", payload)
    expected_head = payload["expected_head"]
    target = payload["target_branch"]
    reason = payload["reason"]
    operating = read_operating(project)
    if operating is None:
        located = specimens.locate(project, slug)
        if located.stage == "stable":
            branch = gitops.current_branch(project)
            head = gitops.head_commit(project)
            stable_path = specimens.culture_relative("stable", slug)
            if branch != target or gitops.commit_subject(project, head) != reason or stable_path not in gitops.commit_paths(project, head):
                raise IsotopeError("deploy-conflict", "The stable specimen does not match this deploy retry.", EXIT_CONFLICT, {"slug": slug, "target_branch": target})
            return {"slug": slug, "state": "deployed", "target_branch": branch, "landed_commit": head}
        raise IsotopeError("no-armed-operation", "Deploy requires the matching operation.", EXIT_REFUSED, {"slug": slug})
    if operating["slug"] != slug:
        raise IsotopeError("operation-mismatch", "Deploy targets a different operation.", EXIT_REFUSED, {"slug": slug, "armed": operating["slug"]})
    if operating["state"] == "parked":
        raise IsotopeError("operation-not-armed", "Resume the parked operation before deploy.", EXIT_REFUSED, {"slug": slug})
    if operating["state"] in ("landing", "landed"):
        if (
            operating["target_branch"] != target
            or operating["landing_reason"] != reason
            or gitops.commit_parent(project, operating["operation_head"]) != expected_head
        ):
            raise IsotopeError("deploy-conflict", "The in-progress deploy carries different landing input.", EXIT_CONFLICT)
    else:
        if target == operating["branch"]:
            raise IsotopeError("deploy-refused", "The landing target must differ from the operation branch.", EXIT_REFUSED)
        if gitops.current_branch(project) != operating["branch"]:
            raise IsotopeError("branch-mismatch", "Deploy requires the operation branch to be current.", EXIT_CONFLICT)
        if gitops.index_paths(project) or gitops.baseline_violations(project):
            raise IsotopeError("dirty-deploy", "Deploy requires committed Git-visible work.", EXIT_REFUSED, {"staged": gitops.index_paths(project), "changed": gitops.baseline_violations(project)})
        target_head = gitops.branch_commit(project, target)
        if target_head != operating["base_commit"]:
            raise IsotopeError("target-moved", "The landing target must still equal the operation base.", EXIT_CONFLICT, {"target": target, "expected": operating["base_commit"], "actual": target_head})
        located = specimens.locate(project, slug)
        value, current = specimens.read_validated(located)
        if current != operating["specimen_revision"]:
            raise IsotopeError("chain-broken", "Deploy requires the operating specimen revision.", EXIT_CONFLICT)
        specimens.require_deployable(value)
        if located.stage == "flux":
            specimens.stabilize(project, slug, {"expected_revision": current})
        elif located.stage != "stable":
            raise IsotopeError("wrong-stage", "Deploy requires flux or its retry-safe stable continuation.", EXIT_REFUSED)
        stable_path = specimens.culture_relative("stable", slug)
        flux_path = specimens.culture_relative("flux", slug)
        deployment_paths = gitops.deployment_paths(project)
        if stable_path not in deployment_paths:
            deployment_paths.append(stable_path)
        if gitops.is_tracked(project, flux_path):
            deployment_paths.append(flux_path)
        deployment_paths = sorted(set(deployment_paths))
        committed = gitops.semantic_commit(
            project,
            expected_head=expected_head,
            files=deployment_paths,
            reason=f"isotope: stabilize {slug}",
            allow_managed=True,
            force_add=True,
        )
        operation_head = committed["commit"]
        if not gitops.is_ancestor(project, operating["base_commit"], operation_head):
            raise IsotopeError("history-diverged", "The operation HEAD does not descend from its base.", EXIT_CONFLICT)
        landed_commit = gitops.deterministic_squash_commit(project, operation_head, operating["base_commit"], reason)
        operating = _landing_state(project, operating, target=target, operation_head=operation_head, landed_commit=landed_commit, reason=reason)
    if operating["state"] == "landing":
        gitops.advance_branch(project, operating["target_branch"], operating["landed_commit"], operating["base_commit"])
        operating = _mark_landed(project, operating)
    return cleanup(project, slug)
