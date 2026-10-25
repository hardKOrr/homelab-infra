"""Validation helpers for the Caddy role's opt-in public DNS contract."""

from collections.abc import Mapping
from ipaddress import ip_address, ip_network
import re
from urllib.parse import urlsplit


PUBLIC_DNS_KEYS = {
    "enabled",
    "provider",
    "ttl",
    "families",
    "proxied",
    "adopt_existing",
    "allow_private",
    "address",
}
ADDRESS_KEYS = {"source", "address", "urls", "target"}
DNS_PROVIDER_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
RESTRICTED_ADDRESS_NETWORKS = tuple(
    ip_network(network)
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


def _is_restricted_address(value):
    """Return whether an address is private, local, or carrier-grade NAT."""
    if value.version == 6 and value.ipv4_mapped is not None:
        value = value.ipv4_mapped
    return any(
        value.version == network.version and value in network
        for network in RESTRICTED_ADDRESS_NETWORKS
    )


def _hostname(value):
    """Return an ASCII hostname or None when value is not a valid hostname."""
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if value.endswith(".."):
        return None
    value = value[:-1] if value.endswith(".") else value
    if not value or len(value) > 253:
        return None
    try:
        ascii_name = value.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    labels = ascii_name.split(".")
    if any(
        not label or len(label) > 63 or not HOST_LABEL.fullmatch(label)
        for label in labels
    ):
        return None
    return ascii_name


def _valid_url_host(host):
    if not isinstance(host, str) or not host:
        return False
    try:
        ip_address(host)
        return True
    except ValueError:
        return _hostname(host) is not None


def _public_dns_error(estate):
    name = estate.get("name", "<estate>")
    path = f"domains.{name}.public_dns"
    config = estate.get("public_dns")
    if not isinstance(config, Mapping):
        return f"{path} must be a mapping."

    unknown = sorted(set(config) - PUBLIC_DNS_KEYS, key=str)
    if unknown:
        return f"{path} has unsupported key '{unknown[0]}'."

    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        return f"{path}.enabled must be true or false."

    address = config.get("address")
    if isinstance(address, Mapping):
        unknown_address = sorted(set(address) - ADDRESS_KEYS, key=str)
        if unknown_address:
            return f"{path}.address has unsupported key '{unknown_address[0]}'."

    if not enabled:
        return None

    provider = config.get("provider", estate.get("dns_challenge_provider"))
    if not isinstance(provider, str) or not DNS_PROVIDER_NAME.fullmatch(provider):
        return (
            f"{path}.provider must name a Caddy DNS-01 provider module."
        )

    if not estate.get("has_dns_challenge_credentials", False):
        return (
            f"{path}.enabled requires provider credentials in this estate's "
            "dns_challenge block."
        )

    ttl = config.get("ttl", 300)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
        return f"{path}.ttl must be a positive integer."

    for key in ("proxied", "adopt_existing", "allow_private"):
        if not isinstance(config.get(key, False), bool):
            return f"{path}.{key} must be true or false."

    families = config.get("families", ["ipv4"])
    if (
        not isinstance(families, (list, tuple))
        or not families
        or any(family not in ("ipv4", "ipv6") for family in families)
    ):
        return f"{path}.families must be a non-empty list containing only ipv4 and/or ipv6."

    if not isinstance(address, Mapping):
        return f"{path}.address.source must be static, lookup, or cname."

    source = address.get("source")
    if source not in ("static", "lookup", "cname"):
        return f"{path}.address.source must be static, lookup, or cname."

    source_keys = {
        "static": {"source", "address"},
        "lookup": {"source", "urls"},
        "cname": {"source", "target"},
    }[source]
    unknown_source_keys = sorted(set(address) - source_keys, key=str)
    if unknown_source_keys:
        return f"{path}.address has unsupported key '{unknown_source_keys[0]}'."

    if source == "static":
        static_address = address.get("address")
        if not isinstance(static_address, str):
            return f"{path}.address.address must be a valid IP address."
        try:
            value = ip_address(static_address)
        except (TypeError, ValueError):
            return f"{path}.address.address must be a valid IP address."
        if getattr(value, "scope_id", None) is not None:
            return f"{path}.address.address must be a valid IP address."
        family = "ipv4" if value.version == 4 else "ipv6"
        if family not in families:
            return f"{path}.address.address must match one of public_dns.families."
        if not config.get("allow_private", False) and _is_restricted_address(value):
            return (
                f"{path}.address.address must not be private, loopback, link-local, "
                "or CGNAT unless public_dns.allow_private is true."
            )
        return None

    if source == "lookup":
        urls = address.get("urls")
        if not isinstance(urls, (list, tuple)) or isinstance(urls, str):
            return f"{path}.address.urls must be a list of HTTPS echo URLs."
        if len(urls) < 2:
            return f"{path}.address.urls must contain at least two HTTPS echo URLs."
        for url in urls:
            try:
                parsed = urlsplit(url) if isinstance(url, str) else None
                valid = (
                    parsed is not None
                    and parsed.scheme.lower() == "https"
                    and _valid_url_host(parsed.hostname)
                    and not any(character.isspace() for character in url)
                )
                if valid:
                    parsed.port  # Validate a present port without restricting its value.
            except ValueError:
                valid = False
            if not valid:
                return f"{path}.address.urls must contain valid HTTPS echo URLs."
        return None

    target = _hostname(address.get("target"))
    estate_domain = _hostname(estate.get("domain"))
    if (
        target is None
        or estate_domain is None
        or target == estate_domain
        or target.endswith("." + estate_domain)
    ):
        return (
            f"{path}.address.target must be a valid hostname outside this estate's "
            "own domain."
        )
    try:
        ip_address(target)
    except ValueError:
        return None
    return (
        f"{path}.address.target must be a valid hostname outside this estate's "
        "own domain."
    )


def public_dns_errors(estates):
    """Return safe, value-free validation messages for resolved estate inputs."""
    if not isinstance(estates, (list, tuple)):
        return ["Caddy public DNS estates must be a list."]
    errors = []
    for estate in estates:
        if not isinstance(estate, Mapping):
            errors.append("Caddy public DNS estate entries must be mappings.")
            continue
        error = _public_dns_error(estate)
        if error:
            errors.append(error)
    return errors


class FilterModule:
    """Expose the public DNS contract validator to Caddy role tasks."""

    def filters(self):
        return {"public_dns_errors": public_dns_errors}
