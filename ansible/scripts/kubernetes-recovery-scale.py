#!/usr/bin/env python3
"""Quiesce or restart the named Deployments for a Kubernetes recovery backup Job."""

import json
import os
import re
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
TIMEOUT_SECONDS = 300


def api(method, namespace, deployment, scale=False, payload=None):
    resource = f"deployments/{quote(deployment, safe='')}"
    if scale:
        resource += "/scale"
    url = (
        "https://kubernetes.default.svc/apis/apps/v1/namespaces/"
        f"{quote(namespace, safe='')}/{resource}"
    )
    with open(TOKEN_PATH, encoding="utf-8") as token_file:
        token = token_file.read().strip()
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/merge-patch+json",
            "Accept": "application/json",
        },
    )
    context = ssl.create_default_context(cafile=CA_PATH)
    try:
        with urlopen(request, context=context, timeout=15) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(
            f"Kubernetes API {method} failed for Deployment {deployment}: {error}"
        ) from error


def deployment_names():
    names = json.loads(os.environ.get("K8S_RECOVERY_DEPLOYMENTS", "[]"))
    if not isinstance(names, list) or not names:
        raise RuntimeError("K8S_RECOVERY_DEPLOYMENTS must be a non-empty JSON list")
    if any(
        not isinstance(name, str)
        or not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name)
        for name in names
    ):
        raise RuntimeError("K8S_RECOVERY_DEPLOYMENTS contains an invalid Deployment name")
    return names


def declared_replicas():
    try:
        replicas = int(os.environ.get("K8S_RECOVERY_STOP_REPLICAS", "1"))
    except ValueError as error:
        raise RuntimeError("K8S_RECOVERY_STOP_REPLICAS must be a positive integer") from error
    if replicas < 1:
        raise RuntimeError("K8S_RECOVERY_STOP_REPLICAS must be a positive integer")
    return replicas


def wait_for(namespace, names, replicas):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    remaining = set(names)
    while remaining and time.monotonic() < deadline:
        for name in list(remaining):
            deployment = api("GET", namespace, name)
            generation = int(deployment.get("metadata", {}).get("generation", 0))
            status = deployment.get("status", {})
            observed = int(status.get("observedGeneration", 0))
            actual = int(status.get("replicas", 0))
            available = int(status.get("availableReplicas", 0))
            if observed >= generation and actual == replicas and available >= replicas:
                remaining.remove(name)
        if remaining:
            time.sleep(2)
    if remaining:
        raise RuntimeError(
            f"Timed out waiting for Deployment(s) {', '.join(sorted(remaining))} "
            f"to reach replicas={replicas}"
        )


def scale(namespace, names, replicas):
    for name in names:
        api("PATCH", namespace, name, scale=True, payload={"spec": {"replicas": replicas}})
    wait_for(namespace, names, replicas)


def stop(namespace, names, replicas):
    for name in names:
        deployment = api("GET", namespace, name)
        desired = deployment.get("spec", {}).get("replicas")
        current = int(1 if desired is None else desired)
        # A hard-killed earlier backup can leave the Deployment scaled to zero, before
        # its exit handler resumes it. Use the role's declared count to recover it.
        if current == 0:
            current = replicas
        if current != replicas:
            raise RuntimeError(
                f"Deployment {name} has {current} desired replicas; expected {replicas} before backup"
            )
    stopped = []
    try:
        for name in names:
            api("PATCH", namespace, name, scale=True, payload={"spec": {"replicas": 0}})
            stopped.append(name)
        wait_for(namespace, names, 0)
    except Exception:
        if stopped:
            try:
                scale(namespace, stopped, replicas)
            except Exception as rollback_error:
                print(
                    f"ERROR: rollback could not restart the stopped workload: {rollback_error}",
                    file=sys.stderr,
                )
        raise
    print(f"quiesced Deployment(s): {', '.join(names)}")


def start(namespace, names, replicas):
    scale(namespace, names, replicas)
    print(f"restarted Deployment(s): {', '.join(names)}")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"stop", "start"}:
        raise RuntimeError("usage: kubernetes-recovery-scale.py stop|start")
    namespace = os.environ.get("POD_NAMESPACE", "")
    if not namespace:
        raise RuntimeError("POD_NAMESPACE is missing")
    names = deployment_names()
    replicas = declared_replicas()
    if sys.argv[1] == "stop":
        stop(namespace, names, replicas)
    else:
        start(namespace, names, replicas)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
