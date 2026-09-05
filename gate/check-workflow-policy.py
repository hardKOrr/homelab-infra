#!/usr/bin/env python3
"""Enforce the hosted-lane workflow policy issue #34's second acceptance criterion needs.

Every `.github/workflows/*.yml` file must:

  - Declare an explicit `permissions:` block (top-level or on every job) rather than
    inheriting the default token, which GitHub grants read/write on most scopes for a
    classic (non-fork) repository.
  - Never reference the `secrets` context in a job that a `pull_request` trigger can run.
    A `pull_request`-triggered job runs untrusted PR-branch code with the base
    repository's permissions; injecting a secret into that job is the exact hosted-lane
    secret-injection risk #34 exists to close off. `pull_request_target` and other
    triggers are unaffected — see docs/specs/secrets-handling.md for the self-hosted/
    approval policy that governs those.
  - Never target a self-hosted runner (`runs-on: self-hosted` or a label list containing
    it) without that job also declaring `environment:`, so GitHub's required-reviewer
    approval gate stands between an untrusted trigger and the runner.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
# Overridable so gate/test-workflow-policy.sh can point this at a throwaway fixture
# directory instead of the repository's own (currently compliant) workflow.
WORKFLOWS = Path(os.environ.get("GATE_WORKFLOWS_DIR", REPO / ".github" / "workflows"))


def _contains_secrets_ref(node: object) -> bool:
    if isinstance(node, str):
        return "secrets." in node or "${{ secrets" in node or "${{secrets" in node
    if isinstance(node, dict):
        return any(_contains_secrets_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_secrets_ref(v) for v in node)
    return False


def _runs_on_self_hosted(runs_on: object) -> bool:
    # GitHub Actions runner labels are matched case-insensitively, so "SELF-HOSTED" and
    # "Self-Hosted" select the same runners "self-hosted" does.
    if isinstance(runs_on, str):
        return "self-hosted" in runs_on.lower()
    if isinstance(runs_on, list):
        return any("self-hosted" in str(item).lower() for item in runs_on)
    if isinstance(runs_on, dict):
        # `runs-on: {group: ..., labels: [...]}` selects a runner group/label set rather
        # than GitHub-hosted infrastructure. A `group` key alone, with no explicit
        # "GitHub Actions" default label, is treated conservatively as self-hosted: this
        # policy exists to gate access to a runner this repository does not control, and
        # an unrecognised selector must fail closed, not pass silently.
        if "group" in runs_on:
            return True
        labels = runs_on.get("labels")
        if isinstance(labels, str):
            return "self-hosted" in labels.lower()
        if isinstance(labels, list):
            return any("self-hosted" in str(item).lower() for item in labels)
        return False
    return False


def _pull_request_triggers(on: object) -> bool:
    if isinstance(on, str):
        return on == "pull_request"
    if isinstance(on, list):
        return "pull_request" in on
    if isinstance(on, dict):
        return "pull_request" in on
    return False


def check_workflow(path: Path) -> list[str]:
    findings: list[str] = []
    data = yaml.safe_load(path.read_text()) or {}
    # PyYAML parses the bare `on:` key as boolean True under YAML 1.1.
    on = data.get("on", data.get(True))
    top_permissions = data.get("permissions")
    top_env = data.get("env")
    is_pr_triggered = _pull_request_triggers(on)
    jobs = data.get("jobs") or {}

    # Workflow-level `env` is inherited by every job and step (GitHub Actions context
    # availability), so a secrets reference placed there reaches pull_request-triggered
    # code exactly as if it were written inside the job.
    if is_pr_triggered and _contains_secrets_ref(top_env):
        findings.append(
            f"{path.name}: workflow-level env references the secrets context, "
            "inherited by every pull_request-triggered job"
        )

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_permissions = job.get("permissions", top_permissions)
        if job_permissions is None:
            findings.append(
                f"{path.name}: job '{job_name}' has no explicit permissions "
                "(top-level or job-level) — declare the minimal scopes it needs"
            )

        if is_pr_triggered and _contains_secrets_ref(job):
            findings.append(
                f"{path.name}: job '{job_name}' is pull_request-triggered and "
                "references the secrets context"
            )

        if _runs_on_self_hosted(job.get("runs-on")) and "environment" not in job:
            findings.append(
                f"{path.name}: job '{job_name}' targets a self-hosted runner without "
                "an environment approval gate"
            )

    return findings


def main() -> int:
    if not WORKFLOWS.is_dir():
        print("check-workflow-policy: no .github/workflows/ directory found")
        return 0

    findings: list[str] = []
    workflow_files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    for workflow in workflow_files:
        findings.extend(check_workflow(workflow))

    if findings:
        print("check-workflow-policy: hosted-lane policy violation(s):", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1

    print(f"check-workflow-policy: {len(workflow_files)} workflow(s) scanned, 0 problem(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
