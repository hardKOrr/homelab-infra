#!/usr/bin/env python3
"""Job-local stateful HTTPS mock of the Proxmox REST endpoints this platform's Ansible
tasks, the community.proxmox.proxmox module, and the community.proxmox inventory plugin
exercise.

This is NOT a Proxmox implementation. It reproduces exactly the request/response shapes
those two production consumers need to run for real against it, over TLS with a
throwaway self-signed certificate (real Proxmox is TLS-only; every production task
already sets validate_certs: false for that reason). It never emulates pct, qm, pvesh,
pveam, QEMU guest agent timing, LXC behavior, storage drivers, cloud-init, or network
hardware, and it never contacts a real Proxmox or provider endpoint.

State (guests, storage, backup jobs, and the request log) and the generated certificate
live only for the lifetime of one ProxmoxMock instance, in memory and a job-local
temporary directory respectively, and both are discarded when it is closed.
"""
from __future__ import annotations

import json
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

OWNER_TAG = "_+lab"

_RESOURCES_RE = re.compile(r"^/api2/json/cluster/resources$")
_NEXTID_RE = re.compile(r"^/api2/json/cluster/nextid$")
_BACKUP_RE = re.compile(r"^/api2/json/cluster/backup$")
_BACKUP_ITEM_RE = re.compile(r"^/api2/json/cluster/backup/(?P<job>[^/]+)$")
_STORAGE_RE = re.compile(r"^/api2/json/nodes/(?P<node>[^/]+)/storage$")
_STORAGE_CONTENT_RE = re.compile(
    r"^/api2/json/nodes/(?P<node>[^/]+)/storage/(?P<storage>[^/]+)/content$"
)
_GUEST_LIST_RE = re.compile(r"^/api2/json/nodes/(?P<node>[^/]+)/(?P<kind>lxc|qemu)$")
_GUEST_ITEM_RE = re.compile(
    r"^/api2/json/nodes/(?P<node>[^/]+)/(?P<kind>lxc|qemu)/(?P<vmid>\d+)/config$"
)
_GUEST_STATUS_RE = re.compile(
    r"^/api2/json/nodes/(?P<node>[^/]+)/(?P<kind>lxc|qemu)/(?P<vmid>\d+)/status/current$"
)
_GUEST_SNAPSHOT_RE = re.compile(
    r"^/api2/json/nodes/(?P<node>[^/]+)/(?P<kind>lxc|qemu)/(?P<vmid>\d+)/snapshot$"
)
_GUEST_INTERFACES_RE = re.compile(
    r"^/api2/json/nodes/(?P<node>[^/]+)/lxc/(?P<vmid>\d+)/interfaces$"
)
_TASK_STATUS_RE = re.compile(r"^/api2/json/nodes/(?P<node>[^/]+)/tasks/(?P<upid>[^/]+)/status$")


def _error(status, message):
    return status, {"data": None, "errors": {"error": message}}


class ProxmoxMockState:
    """In-memory Proxmox-shaped state plus a structured, redacted request log."""

    def __init__(self):
        self.lock = threading.Lock()
        self.nodes = {}  # node name -> {"node": name, "type": "node", "status": "online"}
        self.guests = {}  # (node, kind, vmid) -> guest dict
        self.storage = {}  # (node, storage) -> storage dict
        self.backup_jobs = {}  # job id -> job dict
        self.requests = []  # [{method, path, mutated}]
        self._inject = {}  # (method, path) -> status, single-shot
        self._next_vmid = 200

    # -- fixtures -----------------------------------------------------------
    def seed_node(self, node):
        self.nodes[node] = {"node": node, "type": "node", "status": "online"}

    def seed_guest(self, node, kind, vmid, name, tags, status="stopped", template=False, config=None):
        self.seed_node(node)
        self.guests[(node, kind, str(vmid))] = {
            "node": node,
            "kind": kind,
            "vmid": int(vmid),
            "name": name,
            "tags": tags,
            "status": status,
            "template": template,
            "config": dict(config or {}),
        }

    def seed_storage(self, node, storage, content):
        self.storage[(node, storage)] = {"node": node, "storage": storage, "content": content}

    def seed_storage_content(self, node, storage, volid):
        self.storage.setdefault(
            (node, storage), {"node": node, "storage": storage, "content": [], "volids": []}
        ).setdefault("volids", []).append(volid)

    def seed_backup_job(self, job_id, schedule, storage):
        self.backup_jobs[job_id] = {"id": job_id, "schedule": schedule, "storage": storage}

    # -- error injection ------------------------------------------------------
    def inject_once(self, method, path, status):
        """Make the NEXT matching request fail with `status`. Consumed on use."""
        self._inject[(method, path)] = status

    def _pop_injection(self, method, path):
        return self._inject.pop((method, path), None)

    # -- request log ----------------------------------------------------------
    def log(self, method, path, mutated):
        # No fixture in this contract carries a hostname, token, key, or user config, so
        # nothing here is redacted beyond never recording request bodies verbatim.
        self.requests.append({"method": method, "path": path, "mutated": mutated})

    def requests_since(self, since=0):
        return self.requests[since:]

    def mutations(self, since=0):
        return [r for r in self.requests[since:] if r["mutated"]]

    def snapshot(self):
        """A comparable, order-independent view of all state, for the two-run assertion."""
        return {
            "guests": {"|".join(map(str, k)): v for k, v in sorted(self.guests.items())},
            "storage": {"|".join(map(str, k)): v for k, v in sorted(self.storage.items())},
            "backup_jobs": dict(sorted(self.backup_jobs.items())),
        }


def _tags_str(tags):
    return ";".join(tags) if isinstance(tags, (list, tuple)) else tags


class _Handler(BaseHTTPRequestHandler):
    state: ProxmoxMockState = None

    def log_message(self, *_a):
        pass  # the mock keeps its own structured request log instead

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode())
        except ValueError:
            # requests/proxmoxer send a list-valued form field (tags=a&tags=b&...) rather
            # than one delimited value, but real Proxmox stores and returns `tags` as a
            # single `;`-delimited string (community.proxmox's own _LIST_FIELDS = {"tags":
            # ";"} assumes exactly this) — so that one field is rejoined here to match
            # the shape the module's update-diff logic reads back from GET .../config.
            parsed = parse_qs(raw.decode())
            return {
                key: (";".join(values) if key == "tags" else values[0])
                for key, values in parsed.items()
            }

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        state = self.state

        injected = state._pop_injection(method, path)
        if injected:
            state.log(method, path, mutated=False)
            self._send(*_error(injected, "injected failure for contract test"))
            return

        body = self._read_json() if method in ("POST", "PUT") else {}

        with state.lock:
            status, payload, mutated = self._route(method, path, query, body, state)
        state.log(method, path, mutated)
        self._send(status, payload)

    def _route(self, method, path, query, body, state):  # noqa: PLR0911, PLR0912
        if method == "GET" and path == "/api2/json/version":
            # community.proxmox's connect() always probes this; tags/timezone/etc. need
            # >= 6.1/6.3, so the mock reports a current release.
            return 200, {"data": {"version": "8.2.4", "release": "8.2", "repoid": "mock"}}, False

        if method == "GET" and path == "/api2/json/nodes":
            return 200, {"data": list(state.nodes.values())}, False

        if method == "GET" and path == "/api2/json/pools":
            return 200, {"data": []}, False

        if method == "GET" and _NEXTID_RE.match(path):
            state._next_vmid += 1
            return 200, {"data": str(state._next_vmid)}, False

        if method == "GET" and _RESOURCES_RE.match(path):
            want = query.get("type")
            rows = []
            for (node, kind, _vmid), guest in state.guests.items():
                if want and want != "vm":
                    continue
                rows.append(
                    {
                        "node": node,
                        "type": kind,
                        "vmid": guest["vmid"],
                        "name": guest["name"],
                        "tags": _tags_str(guest["tags"]),
                        "status": guest["status"],
                        "template": 1 if guest["template"] else 0,
                    }
                )
            return 200, {"data": rows}, False

        m = _GUEST_LIST_RE.match(path)
        if m:
            node, kind = m.group("node"), m.group("kind")
            if method == "GET":
                rows = [
                    {
                        "vmid": g["vmid"],
                        "name": g["name"],
                        "tags": _tags_str(g["tags"]),
                        "status": g["status"],
                        "template": 1 if g["template"] else 0,
                    }
                    for (n, k, _v), g in state.guests.items()
                    if n == node and k == kind
                ]
                return 200, {"data": rows}, False
            if method == "POST":
                vmid = str(body.get("vmid"))
                key = (node, kind, vmid)
                if key in state.guests:
                    return _error(400, f"config file already exists for {kind} {vmid}") + (False,)
                config = {k: v for k, v in body.items() if k != "vmid"}
                state.guests[key] = {
                    "node": node,
                    "kind": kind,
                    "vmid": int(vmid),
                    "name": body.get("hostname") or body.get("name"),
                    "tags": body.get("tags", ""),
                    "status": "stopped",
                    "template": False,
                    "config": config,
                }
                return 200, {"data": f"UPID:{node}:mock:create:{kind}:{vmid}::"}, True

        m = _GUEST_ITEM_RE.match(path)
        if m:
            node, kind, vmid = m.group("node"), m.group("kind"), m.group("vmid")
            key = (node, kind, vmid)
            if method == "GET":
                guest = state.guests.get(key)
                if guest is None:
                    return _error(500, f"{kind} {vmid} does not exist") + (False,)
                return 200, {"data": {**guest["config"], "tags": _tags_str(guest["tags"])}}, False
            if method == "PUT":
                guest = state.guests.get(key)
                if guest is None:
                    return _error(500, f"{kind} {vmid} does not exist") + (False,)
                desired_tags = body.get("tags", _tags_str(guest["tags"]))
                desired_config = {**guest["config"], **body}
                unchanged = (
                    desired_tags == _tags_str(guest["tags"])
                    and desired_config == guest["config"]
                )
                guest["tags"] = desired_tags
                guest["config"] = desired_config
                return 200, {"data": None}, not unchanged

        m = _GUEST_STATUS_RE.match(path)
        if m and method == "GET":
            guest = state.guests.get((m.group("node"), m.group("kind"), m.group("vmid")))
            if guest is None:
                return _error(500, f"{m.group('kind')} {m.group('vmid')} does not exist") + (False,)
            return 200, {"data": {"status": guest["status"], "qmpstatus": "running"}}, False

        if _GUEST_SNAPSHOT_RE.match(path) and method == "GET":
            return 200, {"data": []}, False

        if _GUEST_INTERFACES_RE.match(path) and method == "GET":
            # Real Proxmox returns 501 when the LXC has no running network namespace to
            # query; the inventory plugin treats that status as "skip, no interfaces".
            return _error(501, "lxc interfaces unavailable in the mock") + (False,)

        m = _TASK_STATUS_RE.match(path)
        if m and method == "GET":
            # Every mutating call this mock serves completes synchronously, so any UPID
            # it hands out is already finished by the time a caller polls for it.
            return 200, {"data": {"status": "stopped", "exitstatus": "OK"}}, False

        if method == "GET" and _STORAGE_RE.match(path):
            node = _STORAGE_RE.match(path).group("node")
            rows = [s for (n, _s), s in state.storage.items() if n == node]
            return 200, {"data": rows}, False

        m = _STORAGE_CONTENT_RE.match(path)
        if m and method == "GET":
            entry = state.storage.get((m.group("node"), m.group("storage")))
            volids = (entry or {}).get("volids", [])
            return 200, {"data": [{"volid": v} for v in volids]}, False

        if _BACKUP_RE.match(path):
            if method == "GET":
                return 200, {"data": list(state.backup_jobs.values())}, False
            if method == "POST":
                job_id = body.get("id")
                if job_id in state.backup_jobs:
                    return _error(400, f"backup job {job_id} already exists") + (False,)
                state.backup_jobs[job_id] = dict(body)
                return 200, {"data": job_id}, True

        m = _BACKUP_ITEM_RE.match(path)
        if m and method == "PUT":
            job_id = m.group("job")
            job = state.backup_jobs.get(job_id)
            if job is None:
                return _error(500, f"backup job {job_id} does not exist") + (False,)
            desired = {**job, **body}
            unchanged = desired == job
            state.backup_jobs[job_id] = desired
            return 200, {"data": None}, not unchanged

        return _error(404, f"no mock route for {method} {path}") + (False,)


def _generate_self_signed_cert(cert_dir):
    """A throwaway TLS keypair for this run only, mirroring the self-signed cert every
    real Proxmox node serves out of the box (which is why every production task here
    already sets validate_certs: false)."""
    key_path = cert_dir / "mock.key"
    cert_path = cert_dir / "mock.crt"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "1", "-subj", "/CN=proxmox-mock.invalid",
            "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert_path, key_path


class ProxmoxMock:
    """Owns the mock server's lifecycle. Use as a context manager in tests."""

    def __init__(self):
        self.state = ProxmoxMockState()
        handler = type("BoundHandler", (_Handler,), {"state": self.state})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._cert_dir = Path(tempfile.mkdtemp(prefix="homelab-proxmox-mock-cert."))

    @property
    def base_url(self):
        host, port = self._server.server_address
        return f"https://{host}:{port}"

    @property
    def host(self):
        return self._server.server_address[0]

    @property
    def port(self):
        return self._server.server_address[1]

    def __enter__(self):
        cert_path, key_path = _generate_self_signed_cert(self._cert_dir)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._server.server_close()
        shutil.rmtree(self._cert_dir, ignore_errors=True)
