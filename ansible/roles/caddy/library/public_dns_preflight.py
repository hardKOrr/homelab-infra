#!/usr/bin/python
"""Read-only public DNS checks and Cloudflare ownership comments for Caddy."""

from http.client import HTTPSConnection
import ipaddress
import json
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from ansible.module_utils.basic import AnsibleModule


CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
OWNERSHIP_COMMENT = "Managed by homelab-infra public DNS"
RESTRICTED_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


class PublicDNSPreflightError(Exception):
    """An error safe to show in Ansible output."""


class _FamilyHTTPSConnection(HTTPSConnection):
    """HTTPSConnection pinned to the address family used by Caddy's lookup."""

    def __init__(self, host, port, family, **kwargs):
        super().__init__(host, port, **kwargs)
        self.address_family = family

    def connect(self):
        addresses = socket.getaddrinfo(
            self.host, self.port, self.address_family, socket.SOCK_STREAM
        )
        last_error = None
        for family, socktype, protocol, _canonical, sockaddr in addresses:
            sock = socket.socket(family, socktype, protocol)
            try:
                sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as error:
                last_error = error
                sock.close()
        if last_error:
            raise last_error
        raise OSError("No address was available for the selected IP family")


def _https_echo_request(url, family, timeout=15):
    parsed = urlsplit(url)
    host = parsed.hostname
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    connection = _FamilyHTTPSConnection(
        host,
        port,
        family,
        timeout=timeout,
        context=ssl.create_default_context(),
    )
    try:
        connection.request("GET", path, headers={"Accept": "text/plain"})
        response = connection.getresponse()
        return response.status, response.read(257)
    finally:
        connection.close()


def _http_request(url, method="GET", headers=None, payload=None, timeout=20):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read(2 * 1024 * 1024 + 1)
    except HTTPError as error:
        return error.code, b""
    except (URLError, TimeoutError, OSError, ssl.SSLError) as error:
        raise PublicDNSPreflightError(
            "Public DNS preflight could not reach the Cloudflare API."
        ) from error


def _is_restricted(address):
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(
        address.version == network.version and address in network
        for network in RESTRICTED_NETWORKS
    )


def _normalise_name(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("invalid DNS name")
    value = value[:-1] if value.endswith(".") else value
    if not value or value.endswith("."):
        raise ValueError("invalid DNS name")
    try:
        name = value.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("invalid DNS name") from error
    if len(name) > 253 or any(not label or len(label) > 63 for label in name.split(".")):
        raise ValueError("invalid DNS name")
    return name


def _desired_values(public_dns, echo_fetcher=None):
    """Return private desired values and a flag used for the safe warning task."""
    config = public_dns.get("address", {})
    source = config.get("source")
    allow_private = public_dns.get("allow_private", False)
    families = public_dns.get("families", ["ipv4"])
    expected = {}

    if source == "static":
        try:
            address = ipaddress.ip_address(config["address"])
        except (KeyError, TypeError, ValueError) as error:
            raise PublicDNSPreflightError(
                "Public DNS preflight requires a valid static IP address."
            ) from error
        expected["A" if address.version == 4 else "AAAA"] = address
    elif source == "lookup":
        urls = config.get("urls", [])
        if echo_fetcher is None:
            echo_fetcher = _https_echo_request
        for family_name in families:
            family = socket.AF_INET if family_name == "ipv4" else socket.AF_INET6
            version = 4 if family_name == "ipv4" else 6
            answers = set()
            for url in urls:
                try:
                    status, body = echo_fetcher(url, family)
                    if status != 200:
                        continue
                    raw = body.decode("ascii").strip()
                    address = ipaddress.ip_address(raw)
                    if address.version == version:
                        answers.add(address)
                except Exception:
                    # Caddy's simple_http source skips a URL that does not return a
                    # valid address for this family, then tries the next endpoint.
                    continue
            family_label = "IPv4" if version == 4 else "IPv6"
            if not answers:
                raise PublicDNSPreflightError(
                    "No configured public DNS lookup endpoint returned "
                    f"{family_label} from the Caddy guest."
                )
            if len(answers) != 1:
                raise PublicDNSPreflightError(
                    f"Public DNS lookup endpoints disagree for {family_label}."
                )
            expected["A" if version == 4 else "AAAA"] = next(iter(answers))
    elif source == "cname":
        try:
            expected["CNAME"] = _normalise_name(config["target"])
        except (KeyError, TypeError, ValueError) as error:
            raise PublicDNSPreflightError(
                "Public DNS preflight requires a valid CNAME target."
            ) from error
        return expected, False
    else:
        raise PublicDNSPreflightError(
            "Public DNS preflight requires a static, lookup, or cname address source."
        )

    restricted = any(
        _is_restricted(value)
        for value in expected.values()
        if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address))
    )
    if restricted and not allow_private:
        raise PublicDNSPreflightError(
            "Public DNS address is private, loopback, link-local, or CGNAT; "
            "set public_dns.allow_private: true to override."
        )
    return expected, restricted


def _api_json(requester, url, token, method="GET", payload=None):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        status, body = requester(url, method, headers, payload)
    except PublicDNSPreflightError:
        raise
    except Exception as error:
        raise PublicDNSPreflightError(
            "Public DNS preflight could not reach the Cloudflare API."
        ) from error

    if status in (401, 403):
        raise PublicDNSPreflightError(
            "Public DNS preflight could not authenticate with the Cloudflare API."
        )
    if status != 200:
        raise PublicDNSPreflightError(
            "Cloudflare API rejected a public DNS preflight request."
        )
    try:
        value = json.loads(body.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicDNSPreflightError(
            "Cloudflare API returned a malformed response during public DNS preflight."
        ) from error
    if not isinstance(value, dict) or value.get("success") is not True:
        if isinstance(value, dict) and value.get("success") is False:
            raise PublicDNSPreflightError(
                "Cloudflare API rejected a public DNS preflight request."
            )
        raise PublicDNSPreflightError(
            "Cloudflare API returned a malformed response during public DNS preflight."
        )
    return value


def _cloudflare_zone_id(domain, token, requester, api_base):
    try:
        name = _normalise_name(domain)
    except (TypeError, ValueError) as error:
        raise PublicDNSPreflightError(
            "Public DNS preflight requires a valid estate domain."
        ) from error
    url = api_base.rstrip("/") + "/zones?" + urlencode({"name": name})
    response = _api_json(requester, url, token)
    zones = response.get("result")
    if not isinstance(zones, list):
        raise PublicDNSPreflightError(
            "Cloudflare API returned a malformed zone list during public DNS preflight."
        )
    matches = []
    try:
        for zone in zones:
            if not isinstance(zone, dict):
                raise ValueError("invalid zone")
            if _normalise_name(zone.get("name")) == name:
                matches.append(zone)
    except (TypeError, ValueError) as error:
        raise PublicDNSPreflightError(
            "Cloudflare API returned a malformed zone list during public DNS preflight."
        ) from error
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("id"), str)
        or not matches[0]["id"]
    ):
        raise PublicDNSPreflightError(
            "Cloudflare zone lookup did not return exactly one matching estate zone."
        )
    return matches[0]["id"]


def _cloudflare_records(zone_id, owner, record_type, token, requester, api_base):
    expected_name = _normalise_name(owner)
    parsed = []
    page = 1
    while True:
        url = api_base.rstrip("/") + "/zones/" + quote(zone_id, safe="") + "/dns_records?"
        url += urlencode(
            {"type": record_type, "name": expected_name, "per_page": 100, "page": page}
        )
        response = _api_json(requester, url, token)
        records = response.get("result")
        result_info = response.get("result_info")
        if not isinstance(records, list):
            raise PublicDNSPreflightError(
                "Cloudflare API returned a malformed DNS record list during public DNS preflight."
            )
        if result_info is None and len(records) < 100:
            total_pages = 1
        elif (
            isinstance(result_info, dict)
            and not isinstance(result_info.get("total_pages"), bool)
            and isinstance(result_info.get("total_pages"), int)
            and result_info["total_pages"] >= 1
        ):
            total_pages = result_info["total_pages"]
        else:
            raise PublicDNSPreflightError(
                "Cloudflare API returned a malformed DNS record list during public DNS preflight."
            )
        try:
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("invalid record")
                kind = record.get("type")
                name = _normalise_name(record.get("name"))
                if name != expected_name:
                    continue
                if kind != record_type:
                    raise ValueError("record type did not match the provider query")
                content = record.get("content")
                if not isinstance(content, str) or not content:
                    raise ValueError("invalid record content")
                if record_type in ("A", "AAAA"):
                    value = ipaddress.ip_address(content)
                    if value.version != (4 if record_type == "A" else 6):
                        raise ValueError("wrong address family")
                else:
                    value = _normalise_name(content)
                comment = record.get("comment")
                if comment is not None and not isinstance(comment, str):
                    raise ValueError("invalid record comment")
                parsed.append(
                    {
                        "id": record.get("id"),
                        "type": record_type,
                        "name": expected_name,
                        "value": value,
                        "comment": comment,
                    }
                )
        except (TypeError, ValueError) as error:
            raise PublicDNSPreflightError(
                "Cloudflare API returned a malformed DNS record during public DNS preflight."
            ) from error
        if page >= total_pages:
            return parsed
        page += 1


def _cloudflare_context(estate, requester, api_base):
    public_dns = estate.get("public_dns", {})
    challenge = estate.get("dns_challenge", {})
    provider = public_dns.get("provider") or (
        challenge.get("provider") if isinstance(challenge, dict) else None
    )
    if provider != "cloudflare":
        raise PublicDNSPreflightError(
            "Public DNS preflight has no read-only record adapter for provider "
            f"'{provider or 'unknown'}'; no records were published."
        )
    token = challenge.get("api_token") if isinstance(challenge, dict) else None
    if not isinstance(token, str) or not token:
        raise PublicDNSPreflightError(
            "Cloudflare public DNS preflight requires this estate's api_token "
            "from dns_challenge."
        )
    zone_id = _cloudflare_zone_id(estate.get("domain"), token, requester, api_base)
    return token, zone_id


def _record_is_owned(record):
    return record.get("comment") == OWNERSHIP_COMMENT


def _records_for_owner(owner, zone_id, token, requester, api_base):
    return {
        record_type: _cloudflare_records(
            zone_id, owner, record_type, token, requester, api_base
        )
        for record_type in ("A", "AAAA", "CNAME")
    }


def preflight(public_dns, estates, requester=None, echo_fetcher=None, api_base=CLOUDFLARE_API):
    """Check the lab-wide address once, then each enabled estate's two DNS names."""
    if requester is None:
        requester = _http_request
    expected, private_override_used = _desired_values(public_dns, echo_fetcher)
    source = public_dns.get("address", {}).get("source")
    candidates = []

    for estate in estates:
        token, zone_id = _cloudflare_context(estate, requester, api_base)
        adopt_existing = estate.get("public_dns", {}).get("adopt_existing", False)
        for owner in (estate.get("domain"), "*." + estate.get("domain", "")):
            try:
                owner = _normalise_name(owner)
            except (TypeError, ValueError) as error:
                raise PublicDNSPreflightError(
                    "Public DNS preflight requires a valid estate domain."
                ) from error
            records = _records_for_owner(owner, zone_id, token, requester, api_base)

            if source == "cname":
                if records["A"] or records["AAAA"]:
                    record_type = "A" if records["A"] else "AAAA"
                    raise PublicDNSPreflightError(
                        f"Public DNS preflight found an {record_type} record at {owner} "
                        "where a CNAME is required; address/CNAME conflicts cannot be adopted."
                    )
                existing = records["CNAME"]
                for record in existing:
                    if record["value"] != expected["CNAME"] and not (
                        adopt_existing or _record_is_owned(record)
                    ):
                        estate_name = estate.get("name", "<estate>")
                        raise PublicDNSPreflightError(
                            f"Public DNS preflight found a different CNAME record at {owner}; "
                            "record value redacted. Set "
                            f"domains.{estate_name}.public_dns.adopt_existing: true to proceed."
                        )
                if not existing:
                    candidates.append(
                        {"estate": estate.get("name"), "name": owner, "type": "CNAME"}
                    )
                continue

            if records["CNAME"]:
                raise PublicDNSPreflightError(
                    f"Public DNS preflight found a CNAME record at {owner} "
                    "where an address record is required; address/CNAME conflicts cannot be adopted."
                )
            for record_type in ("A", "AAAA"):
                existing = records[record_type]
                desired = expected.get(record_type)
                for record in existing:
                    value_matches = desired is not None and record["value"] == desired
                    if not value_matches and not (
                        adopt_existing or _record_is_owned(record)
                    ):
                        estate_name = estate.get("name", "<estate>")
                        raise PublicDNSPreflightError(
                            f"Public DNS preflight found a different {record_type} record at {owner}; "
                            "record value redacted. Set "
                            f"domains.{estate_name}.public_dns.adopt_existing: true to proceed."
                        )
                if desired is not None and not existing:
                    candidates.append(
                        {"estate": estate.get("name"), "name": owner, "type": record_type}
                    )

    return {
        "records_to_mark": candidates,
        "private_override_used": private_override_used,
    }


def mark_created_records(
    public_dns, estates, candidates, requester=None, echo_fetcher=None, api_base=CLOUDFLARE_API
):
    """Tag only records absent at preflight that Caddy has since created as desired."""
    if requester is None:
        requester = _http_request
    if not candidates:
        return 0

    expected, _private_override_used = _desired_values(public_dns, echo_fetcher)
    estates_by_name = {estate.get("name"): estate for estate in estates}
    marked = 0
    zone_ids = {}
    for candidate in candidates:
        estate = estates_by_name.get(candidate.get("estate"))
        if not estate:
            continue
        record_type = candidate.get("type")
        name = candidate.get("name")
        desired = expected.get(record_type)
        if desired is None or record_type not in ("A", "AAAA", "CNAME"):
            continue
        if estate.get("name") not in zone_ids:
            token, zone_id = _cloudflare_context(estate, requester, api_base)
            zone_ids[estate.get("name")] = (token, zone_id)
        token, zone_id = zone_ids[estate.get("name")]
        records = _cloudflare_records(
            zone_id, name, record_type, token, requester, api_base
        )
        for record in records:
            if record["value"] != desired or record.get("comment"):
                continue
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id:
                raise PublicDNSPreflightError(
                    "Cloudflare API returned a malformed DNS record while setting its ownership comment."
                )
            url = (
                api_base.rstrip("/")
                + "/zones/"
                + quote(zone_id, safe="")
                + "/dns_records/"
                + quote(record_id, safe="")
            )
            _api_json(
                requester,
                url,
                token,
                method="PATCH",
                payload={"comment": OWNERSHIP_COMMENT},
            )
            marked += 1
    return marked


def main():
    module = AnsibleModule(
        argument_spec={
            "operation": {"type": "str", "choices": ["preflight", "mark"], "required": True},
            "public_dns": {"type": "dict", "required": True},
            "estates": {"type": "list", "elements": "dict", "required": True},
            "records_to_mark": {"type": "list", "elements": "dict", "default": []},
        },
        supports_check_mode=True,
    )
    try:
        if module.params["operation"] == "preflight":
            result = preflight(module.params["public_dns"], module.params["estates"])
            module.exit_json(changed=False, safe_error="", **result)
        else:
            if module.check_mode:
                module.exit_json(changed=False, safe_error="", marked_count=0)
            marked = mark_created_records(
                module.params["public_dns"],
                module.params["estates"],
                module.params["records_to_mark"],
            )
            module.exit_json(changed=marked > 0, safe_error="", marked_count=marked)
    except PublicDNSPreflightError as error:
        module.exit_json(
            changed=False,
            safe_error=str(error),
            records_to_mark=[],
            private_override_used=False,
            marked_count=0,
        )
    except Exception:
        # Never echo an exception: HTTP libraries can include credentials, URLs,
        # or full record contents in their error strings.
        module.exit_json(
            changed=False,
            safe_error="Public DNS preflight failed because a provider response was invalid.",
            records_to_mark=[],
            private_override_used=False,
            marked_count=0,
        )


if __name__ == "__main__":
    main()
