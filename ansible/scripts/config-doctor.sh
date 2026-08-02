#!/usr/bin/env bash
# config-doctor.sh -- validate the authored config/ tree against ansible/vars/CONTRACT.md §5.
#
# Reports EVERY problem in one pass, each named by file and key path, then exits non-zero
# if any of them is an error. Mutates nothing: it is safe to run at any time, and it runs
# as the first step of every job so a missing key surfaces at the front door rather than
# as a stack trace mid-provision.
#
# Usage:
#   bash ansible/scripts/config-doctor.sh [config-dir]
#
# config-dir defaults to <repo>/config. Secrets are never printed -- only whether they
# resolved, and from where.
#
# Two severities:
#   ERROR   the platform cannot run: a required key is absent from both file and env.
#   WARN    the platform runs, but something is incomplete or will fail later
#           (e.g. vaultwarden.admin_token before bootstrap step 1 has produced it).
#
# Secrets resolve from the environment as well as from a file, which is the recommended
# shape -- see config.example/proxmox.yml:
#   PROXMOX_API_TOKEN        <- proxmox.api_token_secret
#   VAULTWARDEN_ADMIN_TOKEN  <- infrastructure vaultwarden.admin_token
#
# In Seed mode the Vaultwarden admin token may resolve from the temporary slice-013
# sink. In Vault mode lab-run exports the canonical vault value before this doctor runs.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config_dir="${1:-$(cd -- "$script_dir/../.." && pwd)/config}"

py_bin="$(bash "$script_dir/resolve-python.sh")" || exit 1

exec "$py_bin" - "$config_dir" <<'PY'
import os
import sys
import glob

import yaml

CONFIG_DIR = sys.argv[1]
PROBLEMS = []          # (severity, file, key path, message)


def report(severity, where, key, message):
    PROBLEMS.append((severity, where, key, message))


def load(path):
    """Return (data, loaded). A missing file is not itself reported here."""
    if not os.path.isfile(path):
        return {}, False
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        report("ERROR", os.path.basename(path), "-", "not valid YAML: %s" % exc)
        return {}, False
    if data is None:
        return {}, True
    if not isinstance(data, dict):
        report("ERROR", os.path.basename(path), "-",
               "top level must be a mapping, found %s" % type(data).__name__)
        return {}, True
    return data, True


def dig(data, path):
    """Fetch a dotted key path, or None."""
    node = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def need(data, where, path, env_var=None, severity="ERROR"):
    """Require a non-empty value at `path`, optionally satisfiable by an env var."""
    if dig(data, path) not in (None, ""):
        return True
    if env_var and os.environ.get(env_var):
        return True
    hint = " (or set %s)" % env_var if env_var else ""
    report(severity, where, path, "required%s" % hint)
    return False


def vaultwarden_token_sink_holds_a_token():
    """True when the slice-013 token sink exists and carries a non-empty token.

    Mirrors the sink order in ansible/tasks/vaultwarden/token-sink.yml. Read-only and
    failure-tolerant: an unreadable candidate is simply not a match, because this
    function only decides whether to emit a warning.
    """
    candidates = [
        os.environ.get("VAULTWARDEN_TOKEN_SINK"),
        os.path.join(os.environ.get("LAB_SECRETS_DIR", "/etc/homelab-infra/secrets.d"),
                     "vaultwarden.env"),
        os.path.join(CONFIG_DIR, ".generated", "vaultwarden.env"),
    ]
    for path in candidates:
        if not path:
            continue
        try:
            with open(path) as fh:
                for line in fh:
                    if line.startswith("VAULTWARDEN_ADMIN_TOKEN="):
                        if line.split("=", 1)[1].strip():
                            return True
        except OSError:
            continue
    return False


def enum(data, where, path, allowed, required=True):
    value = dig(data, path)
    if value in (None, ""):
        if required:
            report("ERROR", where, path, "required -- one of %s" % " | ".join(allowed))
        return None
    if value not in allowed:
        report("ERROR", where, path,
               "%r is not recognised -- one of %s" % (value, " | ".join(allowed)))
    return value


# ── config/proxmox.yml ────────────────────────────────────────────────────────
PROXMOX_FILE = os.path.join(CONFIG_DIR, "proxmox.yml")
proxmox, found = load(PROXMOX_FILE)
if not found:
    report("ERROR", "proxmox.yml", "-",
           "does not exist at %s -- bootstrap-rundeck.sh writes it; "
           "copy config.example/proxmox.yml if you are authoring it by hand" % PROXMOX_FILE)
else:
    for key in ("proxmox.api_host", "proxmox.node", "proxmox.api_user", "proxmox.api_token_id"):
        need(proxmox, "proxmox.yml", key)
    need(proxmox, "proxmox.yml", "proxmox.api_token_secret", env_var="PROXMOX_API_TOKEN")

    networks = proxmox.get("networks")
    if not isinstance(networks, dict) or not networks:
        report("ERROR", "proxmox.yml", "networks",
               "at least one named network is required")
    else:
        for name, net in networks.items():
            base = "networks.%s" % name
            if not isinstance(net, dict):
                report("ERROR", "proxmox.yml", base, "must be a mapping")
                continue
            for key in ("cidr", "gateway", "dns_servers", "bridge"):
                if net.get(key) in (None, "", []):
                    report("ERROR", "proxmox.yml", "%s.%s" % (base, key), "required")
            if net.get("cidr") == "dhcp":
                report("WARN", "proxmox.yml", "%s.cidr" % base,
                       "dhcp -- every app on this network must set an explicit vmid")

    for key in ("ansible.ssh_user", "ansible.ssh_public_key"):
        need(proxmox, "proxmox.yml", key)


# ── config/infrastructure.yml ─────────────────────────────────────────────────
INFRA_FILE = os.path.join(CONFIG_DIR, "infrastructure.yml")
infra, found = load(INFRA_FILE)
if not found:
    report("ERROR", "infrastructure.yml", "-",
           "does not exist at %s -- bootstrap-rundeck.sh writes it; "
           "copy config.example/infrastructure.yml if you are authoring it by hand" % INFRA_FILE)
else:
    domains = infra.get("domains")
    if not infra.get("domain") and not domains:
        report("ERROR", "infrastructure.yml", "domain",
               "required -- a domain scalar, or a domains map of named estates")
    if domains is not None:
        if not isinstance(domains, dict) or not domains:
            report("ERROR", "infrastructure.yml", "domains",
                   "must be a non-empty mapping of estate name to estate")
        else:
            defaults = [n for n, e in domains.items()
                        if isinstance(e, dict) and e.get("default")]
            if len(defaults) > 1:
                report("ERROR", "infrastructure.yml", "domains",
                       "more than one estate declares default: true (%s)" % ", ".join(defaults))
            if not defaults:
                report("WARN", "infrastructure.yml", "domains",
                       "no estate declares default: true -- the first declared entry "
                       "(%s) will be the default" % next(iter(domains)))
            for name, estate in domains.items():
                if not isinstance(estate, dict) or not estate.get("domain"):
                    report("ERROR", "infrastructure.yml", "domains.%s.domain" % name, "required")

    provider = enum(infra, "infrastructure.yml", "reverse_proxy.provider",
                    ["caddy", "nginx", "none"])
    if provider and provider != "none":
        need(infra, "infrastructure.yml", "reverse_proxy.instance")

    provider = enum(infra, "infrastructure.yml", "sso.provider", ["authentik", "none"])
    if provider == "authentik":
        need(infra, "infrastructure.yml", "sso.instance")

    provider = enum(infra, "infrastructure.yml", "notifications.provider",
                    ["ntfy", "gotify", "discord", "none"])
    if provider and provider != "none":
        need(infra, "infrastructure.yml", "notifications.instance")

    provider = enum(infra, "infrastructure.yml", "dns.provider",
                    ["pihole", "adguard", "opnsense", "none"])
    if provider and provider != "none":
        # An in-lab DNS guest is named by instance and resolved from inventory; an
        # external one (the common case -- OPNsense on the router) needs an address.
        if not dig(infra, "dns.instance") and not dig(infra, "dns.host"):
            report("ERROR", "infrastructure.yml", "dns.host",
                   "required for provider %r unless dns.instance names a guest "
                   "this platform created" % provider)

    need(infra, "infrastructure.yml", "backups.datastore_path")

    # Produced during Seed mode, then supplied from encrypted control-plane storage or
    # the canonical vault item. It never needs to remain in authored config.
    if not vaultwarden_token_sink_holds_a_token():
        need(infra, "infrastructure.yml", "vaultwarden.admin_token",
             env_var="VAULTWARDEN_ADMIN_TOKEN", severity="WARN")


# ── config/apps/<instance>.yml ────────────────────────────────────────────────
# Instance files are override layers over vars/app-defaults/<app>.yml, so almost every
# key is optional. What is checked is shape: a recursive merge means replacing a mapping
# with a scalar silently clobbers the whole subtree (CONTRACT.md, App-level layering).
IDENTITY_MODES = ["none", "catalog", "oidc", "forward_auth"]
MAPPING_KEYS = ["proxmox", "app", "routing", "update", "resources", "network"]

app_files = sorted(glob.glob(os.path.join(CONFIG_DIR, "apps", "*.yml")))
estate_names = set((infra.get("domains") or {}).keys()) if isinstance(infra, dict) else set()

for path in app_files:
    where = "apps/" + os.path.basename(path)
    instance = os.path.basename(path)[:-4]
    data, _ = load(path)
    if not isinstance(data, dict):
        continue

    for key in MAPPING_KEYS:
        if key in data and not isinstance(data[key], dict):
            report("ERROR", where, key,
                   "must be a mapping -- a scalar here clobbers the whole default subtree")

    if "stack" in data and isinstance(data["stack"], dict):
        report("ERROR", where, "stack", "must be a scalar stack name, e.g. media_stack")

    if "routing" in data and isinstance(data["routing"], dict):
        routing = data["routing"]
        if "auth" in routing:
            report("WARN", where, "routing.auth",
                   "superseded by routing.identity (%s) -- nothing reads it"
                   % " | ".join(IDENTITY_MODES))
        if routing.get("identity") not in (None, "") and routing["identity"] not in IDENTITY_MODES:
            report("ERROR", where, "routing.identity",
                   "%r is not recognised -- one of %s"
                   % (routing["identity"], " | ".join(IDENTITY_MODES)))
        estate = routing.get("estate")
        if estate and estate_names and estate not in estate_names:
            report("ERROR", where, "routing.estate",
                   "%r is not declared under domains: in infrastructure.yml (have: %s)"
                   % (estate, ", ".join(sorted(estate_names))))

    if instance != instance.strip() or "/" in instance or " " in instance:
        report("ERROR", where, "-",
               "the filename is the instance name and becomes a hostname -- "
               "use lowercase letters, digits and hyphens only")


# ── Report ────────────────────────────────────────────────────────────────────
errors = [p for p in PROBLEMS if p[0] == "ERROR"]
warnings = [p for p in PROBLEMS if p[0] == "WARN"]

print("config-doctor: %s" % CONFIG_DIR)
print("  proxmox.yml         %s" % ("present" if os.path.isfile(PROXMOX_FILE) else "MISSING"))
print("  infrastructure.yml  %s" % ("present" if os.path.isfile(INFRA_FILE) else "MISSING"))
print("  apps/               %d instance file(s)" % len(app_files))
print("  proxmox token       %s" % (
    "from PROXMOX_API_TOKEN (environment)" if os.environ.get("PROXMOX_API_TOKEN")
    else "from config/proxmox.yml" if dig(proxmox, "proxmox.api_token_secret")
    else "NOT RESOLVED"))
print("")

if not PROBLEMS:
    print("OK -- no problems found.")
    sys.exit(0)

width = max(len(p[1]) for p in PROBLEMS)
for severity in ("ERROR", "WARN"):
    for _, where, key, message in [p for p in PROBLEMS if p[0] == severity]:
        print("%-5s  %-*s  %s: %s" % (severity, width, where, key, message))

print("")
print("%d error(s), %d warning(s)" % (len(errors), len(warnings)))
sys.exit(1 if errors else 0)
PY
