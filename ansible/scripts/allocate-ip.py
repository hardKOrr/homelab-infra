#!/usr/bin/env python3
"""Choose one static address for a new guest.

Reads a JSON request on stdin and writes a JSON decision on stdout:

    {"ip_address": "192.168.0.66", "source": "pool", "pool": "apps"}

Request keys:

    cidr        required — the network's subnet, e.g. "192.168.0.0/20"
    pool        optional — {"name": str, "range": "a-b"} or {"name": str, "cidr": str};
                allocation walks this span only, and it must sit inside `cidr`
    pin         optional — an address the caller demands; honoured exactly or refused
    in_use      addresses already held, from the Proxmox inventory
    reserved    spans never allocated: an address, "a-b", or a CIDR
    gateway     optional — always reserved, whether or not it is listed
    offset      pool-less fallback: skip this many addresses from the network address
    max_hosts   pool-less fallback: stop after this many addresses

Why a script and not Jinja: the allocator has to compare addresses across four
sources and explain which one refused a request. A template can compute the answer
but cannot say why there is none, and "No available IPs found in 192.168.0.0/20" is
the message that made the flat allocator hard to operate.

Failure is an error, never a silent fallback. Handing back an address from outside
the requested pool would put a guest in the wrong VLAN once the estate is segmented,
which is precisely the decay this allocator exists to stop.
"""

import ipaddress
import json
import sys


class AllocationError(Exception):
    """A request that cannot be satisfied, with an operator-readable reason."""


def parse_span(text, label):
    """Expand an address, an "a-b" range, or a CIDR into a first/last pair."""
    text = str(text).strip()
    try:
        if "-" in text:
            first, last = (part.strip() for part in text.split("-", 1))
            first, last = ipaddress.ip_address(first), ipaddress.ip_address(last)
            if last < first:
                raise AllocationError(f"{label} {text!r} ends before it begins")
            return first, last
        if "/" in text:
            net = ipaddress.ip_network(text, strict=False)
            return net.network_address, net.broadcast_address
        address = ipaddress.ip_address(text)
        return address, address
    except ValueError as exc:
        raise AllocationError(f"{label} {text!r} is not an address, range or CIDR: {exc}")


def spans_of(entries, label):
    return [parse_span(entry, label) for entry in entries or []]


def within(address, spans):
    return any(first <= address <= last for first, last in spans)


def decide(request):
    if not request.get("cidr"):
        raise AllocationError("no cidr was supplied for the network")
    try:
        network = ipaddress.ip_network(str(request["cidr"]), strict=False)
    except ValueError as exc:
        raise AllocationError(f"network cidr {request['cidr']!r} is not a subnet: {exc}")

    in_use = spans_of(request.get("in_use"), "in-use address")
    reserved = spans_of(request.get("reserved"), "reserved span")
    # The network and broadcast addresses are structural, and the gateway is the
    # exclusion the flat allocator missed: this lab's own gateway sits inside the
    # span it walks.
    reserved.append((network.network_address, network.network_address))
    reserved.append((network.broadcast_address, network.broadcast_address))
    if request.get("gateway"):
        reserved.append(parse_span(request["gateway"], "gateway"))

    pool = request.get("pool") or None
    pool_name = (pool or {}).get("name")
    if pool:
        span = pool.get("range") or pool.get("cidr")
        if not span:
            raise AllocationError(
                f"pool {pool_name!r} declares neither a range nor a cidr"
            )
        first, last = parse_span(span, f"pool {pool_name!r}")
        if first not in network or last not in network:
            raise AllocationError(
                f"pool {pool_name!r} ({span}) is not inside network {network}"
            )
    else:
        offset = int(request.get("offset") or 0)
        first = network.network_address + offset
        last = network.broadcast_address
        max_hosts = request.get("max_hosts")
        if max_hosts:
            capped = first + int(max_hosts) - 1
            last = min(last, capped)
        if first > network.broadcast_address:
            raise AllocationError(
                f"offset {offset} lands outside network {network}"
            )

    pin = request.get("pin")
    if pin:
        try:
            address = ipaddress.ip_address(str(pin).strip())
        except ValueError as exc:
            raise AllocationError(f"pinned address {pin!r} is not an address: {exc}")
        where = f"pool {pool_name!r}" if pool else f"network {network}"
        if not (first <= address <= last):
            raise AllocationError(f"pinned address {address} is outside {where}")
        if within(address, in_use):
            raise AllocationError(
                f"pinned address {address} is already held by a guest in the "
                f"Proxmox inventory"
            )
        if within(address, reserved):
            raise AllocationError(f"pinned address {address} falls in a reserved span")
        return {"ip_address": str(address), "source": "pin", "pool": pool_name}

    candidate = first
    while candidate <= last:
        if not within(candidate, in_use) and not within(candidate, reserved):
            return {
                "ip_address": str(candidate),
                "source": "pool" if pool else "subnet",
                "pool": pool_name,
            }
        candidate += 1

    where = f"pool {pool_name!r} ({first}-{last})" if pool else f"network {network}"
    raise AllocationError(
        f"every address in {where} is either held by a guest or reserved"
    )


def main():
    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"allocate-ip: the request was not JSON: {exc}", file=sys.stderr)
        return 2
    try:
        json.dump(decide(request), sys.stdout)
    except AllocationError as exc:
        print(f"allocate-ip: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
