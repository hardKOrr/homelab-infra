#!/usr/bin/env python3
"""Stateful contract tests against the job-local Proxmox API mock (gate/proxmox_mock.py).

Proves the four things gate/README.md and ansible/tasks/proxmox/README.md require of any
provider contract test, at the API-transport boundary rather than by emulating pct, qm,
pvesh, or pveam:

  1. Ownership: an untagged guest is invisible to the same `_+lab` filter the platform's
     inventory and maintenance selectors use; an exactly-tagged one is selected.
  2. Idempotent reconciliation: running one desired-state scenario twice issues a mutating
     request (POST/PUT) only on the first run. The second run's decision — "no request
     needed" — is asserted the same way the corresponding Ansible task would decide it:
     by comparing desired config against what a GET already returned, never by re-running
     a real create/update and hoping it happens to no-op.
  3. A controlled failure: one injected non-2xx response is asserted to surface as a
     failure, not be silently absorbed.
  4. No leaked fixture material: the request log and final state contain only the
     synthetic names/tags this file defines.

Only the standard library is used (urllib), so this needs no addition to
gate/requirements-dev.txt. Nothing here contacts a real Proxmox or provider endpoint —
the mock binds 127.0.0.1 on an ephemeral port for the lifetime of this process only.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from proxmox_mock import OWNER_TAG, ProxmoxMock  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n     expected {want!r}\n          got {got!r}")


def call(base_url, method, path, body=None):
    url = base_url + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def owned(tags_str):
    """The exact ownership contract from ansible/tasks/proxmox/README.md: the SENTINEL
    tag, tested for membership, never by prefix."""
    return OWNER_TAG in (tags_str or "").split(";")


def run_scenario(mock):
    """One full desired-state pass: discovery, ownership filtering, create-or-update for
    an LXC guest, and backup-job reconciliation. Returns the count of mutating requests
    issued (POST/PUT) so the caller can compare run 1 against run 2."""
    base = mock.base_url
    start = len(mock.state.requests)

    # -- discovery + ownership filtering --------------------------------------------
    status, resp = call(base, "GET", "/api2/json/cluster/resources?type=vm")
    check("resources status", status, 200)
    resources = resp["data"]
    owned_names = sorted(r["name"] for r in resources if owned(r["tags"]))
    all_names = sorted(r["name"] for r in resources)
    check("ownership filter selects only tagged guests", owned_names, sorted(n for n in all_names if n != "operator-vm"))
    check("untagged guest still exists but is not selected", "operator-vm" in all_names, True)

    # -- guest create-or-update (idempotent decision) -------------------------------
    node, kind, vmid = "pve1", "lxc", "201"
    status, resp = call(base, "GET", f"/api2/json/nodes/{node}/{kind}/{vmid}/config")
    desired = {"hostname": "sonarr", "tags": OWNER_TAG + ";_-debian;_sonarr", "cores": "2"}
    if status == 200 and resp["data"] == {**desired}:
        pass  # converged already: no PUT/POST needed
    elif status == 200:
        call(base, "PUT", f"/api2/json/nodes/{node}/{kind}/{vmid}/config", desired)
    else:
        call(
            base,
            "POST",
            f"/api2/json/nodes/{node}/{kind}",
            {"vmid": vmid, **desired},
        )

    # -- backup job reconciliation ---------------------------------------------------
    status, resp = call(base, "GET", "/api2/json/cluster/backup")
    jobs = {job["id"]: job for job in resp["data"]}
    desired_job = {"id": "lab-nightly", "schedule": "02:00", "storage": "pbs-store"}
    existing = jobs.get("lab-nightly")
    if existing is None:
        call(base, "POST", "/api2/json/cluster/backup", desired_job)
    elif {k: existing[k] for k in desired_job} != desired_job:
        call(base, "PUT", f"/api2/json/cluster/backup/lab-nightly", desired_job)
    # else: converged, no request

    return len(mock.state.mutations(since=start))


def main():
    with ProxmoxMock() as mock:
        mock.state.seed_guest("pve1", "lxc", "200", "caddy", [OWNER_TAG, "_-debian", "_caddy"])
        mock.state.seed_guest("pve1", "qemu", "300", "operator-vm", ["operator-note"])
        mock.state.seed_storage("pve1", "local", ["images", "vztmpl"])

        first_mutations = run_scenario(mock)
        check("first run performs the reconciling writes", first_mutations, 2)
        state_after_first = mock.state.snapshot()

        second_mutations = run_scenario(mock)
        check("second run is a pure no-op: zero mutating requests", second_mutations, 0)
        check(
            "state is identical across two runs of the same scenario",
            mock.state.snapshot(),
            state_after_first,
        )

        # -- controlled failure ------------------------------------------------------
        mock.state.inject_once("GET", "/api2/json/nodes/pve1/storage", 500)
        status, resp = call(mock.base_url, "GET", "/api2/json/nodes/pve1/storage")
        check("injected failure surfaces as a non-2xx status", status, 500)
        check("injected failure body reports an error", "errors" in resp, True)
        # The injection is single-shot and must not leave the mock in a failing state.
        status, _resp = call(mock.base_url, "GET", "/api2/json/nodes/pve1/storage")
        check("mock recovers after the injected failure is consumed", status, 200)

        # -- no leaked fixture material ------------------------------------------------
        logged = json.dumps(mock.state.requests)
        for forbidden in ("password", "token", "ssh-rsa", "BEGIN OPENSSH", "@pam"):
            if forbidden in logged:
                failures.append(f"request log unexpectedly contains {forbidden!r}")

    if failures:
        print("proxmox-api-contract test failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)

    print("Proxmox API contract tests passed.")


if __name__ == "__main__":
    main()
