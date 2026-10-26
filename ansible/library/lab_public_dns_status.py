#!/usr/bin/python
"""Read public DNS through two JSON DNS-over-HTTPS resolvers.

The module is intentionally read-only. For ``lookup`` address sources it repeats the
Caddy dynamic DNS app's ordered, per-IP-family HTTPS lookup from the delegated Caddy
guest, then checks the apex and a random wildcard label through each resolver.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request


DNS_TYPES = {"A": 1, "CNAME": 5, "AAAA": 28}
FAMILY_TYPES = {"ipv4": ("A", socket.AF_INET), "ipv6": ("AAAA", socket.AF_INET6)}
VALID_STATES = {"matches", "stale", "missing", "unreachable-resolver", "disabled"}
REQUEST_TIMEOUT = 8
MAX_RESPONSE_BYTES = 4096


class ResolverUnavailable(Exception):
    """A resolver did not return a usable DNS-over-HTTPS response."""


def _normalise_hostname(value):
    if not isinstance(value, str):
        return None
    value = value.strip().rstrip(".").lower()
    if not value or len(value) > 253:
        return None
    try:
        labels = value.encode("idna").decode("ascii").split(".")
    except UnicodeError:
        return None
    if any(
        len(label) > 63
        or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
        for label in labels
    ):
        return None
    return ".".join(labels)


def _is_https_endpoint(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        # Accessing port validates its syntax and range.
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _record_value(record_type, value):
    if not isinstance(value, str):
        return None
    if record_type == "CNAME":
        return _normalise_hostname(value)
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if (record_type == "A" and address.version != 4) or (
        record_type == "AAAA" and address.version != 6
    ):
        return None
    return str(address)


def _append_query(url, name, record_type):
    separator = "&" if "?" in url else "?"
    return url + separator + urllib.parse.urlencode({"name": name, "type": record_type})


def _read_limited(response, limit=MAX_RESPONSE_BYTES):
    content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("response too large")
    return content


def _family_socket(host, port, timeout, family):
    """Connect using a specific address family, as Caddy's simple_http source does."""
    last_error = None
    try:
        candidates = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
    except OSError as error:
        raise error
    for address_family, socket_type, protocol, _, address in candidates:
        connection = socket.socket(address_family, socket_type, protocol)
        try:
            connection.settimeout(timeout)
            connection.connect(address)
            return connection
        except OSError as error:
            last_error = error
            connection.close()
    if last_error is not None:
        raise last_error
    raise OSError("no address for requested IP family")


class _FamilyHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, *args, family=socket.AF_UNSPEC, **kwargs):
        self._family = family
        super().__init__(host, *args, **kwargs)

    def connect(self):
        self.sock = _family_socket(self.host, self.port, self.timeout, self._family)
        if self._tunnel_host:
            self._tunnel()


class _FamilyHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *args, family=socket.AF_UNSPEC, **kwargs):
        self._family = family
        super().__init__(host, *args, **kwargs)

    def connect(self):
        self.sock = _family_socket(self.host, self.port, self.timeout, self._family)
        if self._tunnel_host:
            self._tunnel()
        server_hostname = self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _FamilyHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, family):
        self._family = family
        super().__init__()

    def http_open(self, request):
        family = self._family
        return self.do_open(
            lambda host, **kwargs: _FamilyHTTPConnection(host, family=family, **kwargs),
            request,
        )


class _FamilyHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, family):
        self._family = family
        super().__init__()

    def https_open(self, request):
        family = self._family
        context = self._context
        return self.do_open(
            lambda host, **kwargs: _FamilyHTTPSConnection(
                host, family=family, context=context, **kwargs
            ),
            request,
        )


def _open_url(request, timeout=REQUEST_TIMEOUT, family=None):
    if family is None:
        return urllib.request.urlopen(request, timeout=timeout)
    opener = urllib.request.build_opener(
        _FamilyHTTPHandler(family), _FamilyHTTPSHandler(family)
    )
    return opener.open(request, timeout=timeout)


def lookup_ip(urls, family, request_fn=None):
    """Return the first valid address for one family from ordered Caddy echo URLs."""
    if request_fn is None:
        request_fn = _request_lookup
    socket_family = FAMILY_TYPES[family][1]
    wanted_version = 4 if family == "ipv4" else 6
    for endpoint in urls:
        if not _is_https_endpoint(endpoint):
            continue
        try:
            value = request_fn(endpoint, socket_family)
            address = ipaddress.ip_address(value.strip())
            if address.version == wanted_version:
                return str(address)
        except (
            OSError,
            ValueError,
            TypeError,
            urllib.error.URLError,
            http.client.HTTPException,
        ):
            continue
    return None


def _request_lookup(endpoint, socket_family):
    request = urllib.request.Request(endpoint, headers={"Accept": "text/plain"})
    with _open_url(request, family=socket_family) as response:
        if getattr(response, "status", 200) != 200:
            raise ResolverUnavailable("lookup endpoint did not return HTTP 200")
        return _read_limited(response, 256).decode("ascii").strip()


def _query_resolver(resolver, name, record_type):
    try:
        resolver_url = resolver["url"]
        request = urllib.request.Request(
            _append_query(resolver_url, name, record_type),
            headers={"Accept": "application/dns-json"},
        )
        with _open_url(request) as response:
            if getattr(response, "status", 200) != 200:
                raise ResolverUnavailable("resolver did not return HTTP 200")
            payload = json.loads(_read_limited(response).decode("utf-8"))
    except (KeyError, TypeError, ValueError, OSError, urllib.error.URLError) as error:
        raise ResolverUnavailable("resolver query failed") from error

    if not isinstance(payload, dict) or isinstance(payload.get("Status"), bool):
        raise ResolverUnavailable("resolver returned invalid JSON")
    try:
        status = int(payload["Status"])
    except (KeyError, TypeError, ValueError) as error:
        raise ResolverUnavailable("resolver returned no DNS status") from error
    if status not in (0, 3):
        raise ResolverUnavailable("resolver returned a DNS error")

    answers = payload.get("Answer", [])
    if answers is None:
        answers = []
    if not isinstance(answers, list):
        raise ResolverUnavailable("resolver returned invalid answers")
    values = set()
    for answer in answers:
        if not isinstance(answer, dict):
            raise ResolverUnavailable("resolver returned an invalid answer")
        try:
            answer_type = int(answer.get("type", 0))
        except (TypeError, ValueError):
            raise ResolverUnavailable("resolver returned an invalid record type")
        if answer_type != DNS_TYPES[record_type]:
            continue
        value = _record_value(record_type, answer.get("data"))
        if value is None:
            raise ResolverUnavailable("resolver returned an invalid record value")
        values.add(value)
    return values


def _resolvers_valid(resolvers):
    if not isinstance(resolvers, (list, tuple)) or len(resolvers) != 2:
        return False
    if not all(
        isinstance(resolver, dict)
        and isinstance(resolver.get("name"), str)
        and bool(resolver["name"].strip())
        and _is_https_endpoint(resolver.get("url"))
        for resolver in resolvers
    ):
        return False
    return len({resolver["url"].lower() for resolver in resolvers}) == 2


def check_public_dns(
    *,
    enabled,
    domain,
    source,
    static_address="",
    cname_target="",
    lookup_urls=None,
    families=None,
    resolvers=None,
    wildcard_label=None,
    query_fn=None,
    lookup_fn=None,
):
    """Return one of the public status states without exposing DNS values."""
    if not enabled:
        return "disabled"
    if not _resolvers_valid(resolvers):
        return "unreachable-resolver"
    domain = _normalise_hostname(domain)
    if domain is None:
        return "stale"
    if query_fn is None:
        query_fn = _query_resolver
    if lookup_fn is None:
        lookup_fn = lookup_ip
    if not isinstance(families, (list, tuple)) or not families:
        families = ["ipv4"]
    families = list(dict.fromkeys(family for family in families if family in FAMILY_TYPES))
    if not families and source != "cname":
        return "stale"

    if source == "cname":
        expected = {"CNAME": _normalise_hostname(cname_target)}
        record_types = ["CNAME"]
    elif source == "static":
        expected = {}
        try:
            address = ipaddress.ip_address(static_address)
            expected["A" if address.version == 4 else "AAAA"] = str(address)
        except (TypeError, ValueError):
            return "stale"
        # The Caddy preflight treats a static address as exactly one RR type. The
        # families setting can also enable the other family, but a single static IP
        # cannot supply that family's expected value.
        if expected.keys().isdisjoint(FAMILY_TYPES[family][0] for family in families):
            return "stale"
        record_types = list(expected)
    elif source == "lookup":
        lookup_urls = lookup_urls if isinstance(lookup_urls, (list, tuple)) else []
        expected = {}
        for family in families:
            expected[FAMILY_TYPES[family][0]] = lookup_fn(lookup_urls, family)
        record_types = [FAMILY_TYPES[family][0] for family in families]
    else:
        return "stale"

    if any(value is None for value in expected.values()):
        # A missing CNAME target is invalid config; a lookup family may be unavailable
        # from the guest and is still checked for an absent or stale DNS answer below.
        if source == "cname":
            return "stale"

    wildcard_label = wildcard_label or secrets.token_hex(6)
    wildcard_name = _normalise_hostname(f"{wildcard_label}.{domain}")
    if wildcard_name is None:
        return "stale"
    names = [domain, wildcard_name]

    unreachable = False
    missing = False
    stale = False
    for resolver in resolvers:
        for name in names:
            for record_type in record_types:
                try:
                    answers = query_fn(resolver, name, record_type)
                except ResolverUnavailable:
                    unreachable = True
                    continue
                except Exception:  # A read-only status check must not fail its play.
                    unreachable = True
                    continue
                if not answers:
                    missing = True
                    continue
                wanted = expected.get(record_type)
                if wanted is None or answers != {wanted}:
                    stale = True

    if unreachable:
        return "unreachable-resolver"
    if missing:
        return "missing"
    if stale:
        return "stale"
    return "matches"


def main():
    from ansible.module_utils.basic import AnsibleModule

    module = AnsibleModule(
        argument_spec={
            "enabled": {"type": "bool", "default": True},
            "domain": {"type": "str", "required": True},
            "source": {"type": "str", "required": True},
            "static_address": {"type": "str", "default": ""},
            "cname_target": {"type": "str", "default": ""},
            "lookup_urls": {"type": "list", "elements": "str", "default": []},
            "families": {"type": "list", "elements": "str", "default": ["ipv4"]},
            "resolvers": {"type": "list", "elements": "dict", "required": True},
            "wildcard_label": {"type": "str", "default": ""},
        },
        supports_check_mode=True,
    )
    status = check_public_dns(**module.params)
    if status not in VALID_STATES:
        status = "unreachable-resolver"
    module.exit_json(changed=False, status=status)


if __name__ == "__main__":
    main()
