#!/usr/bin/env python3
"""Exercise public DNS lookup, ownership checks, and Cloudflare reads with fakes.

Checks: public address lookup agreement, unsafe address refusal, Cloudflare record ownership
preflight, adoption, safe API failures, delayed record marking and its bounded timeout.
"""

import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import yaml


repo = Path(__file__).resolve().parents[1]
module_path = repo / "ansible/roles/caddy/library/public_dns_preflight.py"
spec = importlib.util.spec_from_file_location("public_dns_preflight", module_path)
if spec is None or spec.loader is None:
    raise SystemExit("could not load public DNS preflight module")
public_dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public_dns)


TOKEN = "fixture-cloudflare-token-secret"
ADDRESS = "198.51.100.20"
OTHER_ADDRESS = "198.51.100.21"
DOMAIN = "alpha.example.test"
API_BASE = "https://api.cloudflare.test/client/v4"


class FakeCloudflare:
    def __init__(self, failure=None):
        self.failure = failure
        self.records = {}
        self.calls = []
        self.patches = []
        self.delayed_records = {}
        self.record_reads = {}

    def delay_record(
        self, record_type, owner, value, *, reads_before_visible=2, record_id
    ):
        key = (record_type, owner)
        self.delayed_records[key] = (
            {
                "id": record_id,
                "type": record_type,
                "name": owner,
                "content": value,
                "comment": None,
            },
            reads_before_visible,
        )
        self.record_reads[key] = 0

    def request(self, url, method, headers, payload):
        self.calls.append((url, method))
        assert headers.get("Authorization") == f"Bearer {TOKEN}"
        if self.failure == "auth":
            return 403, f"{TOKEN} {OTHER_ADDRESS}".encode()
        if self.failure == "transport":
            raise OSError(f"transport detail {TOKEN} {OTHER_ADDRESS}")
        if self.failure == "malformed":
            return 200, b"not-json"

        parsed = urlsplit(url)
        if parsed.path.endswith("/zones"):
            query = parse_qs(parsed.query)
            if query.get("name") != [DOMAIN]:
                return 200, b'{"success":true,"result":[]}'
            return 200, json.dumps({
                "success": True,
                "result": [{"id": "zone-fixture", "name": DOMAIN}],
            }).encode()

        if parsed.path.endswith("/dns_records"):
            query = parse_qs(parsed.query)
            record_type = query["type"][0]
            owner = query["name"][0]
            key = (record_type, owner)
            delayed = self.delayed_records.get(key)
            if delayed:
                record, reads_before_visible = delayed
                self.record_reads[key] += 1
                if self.record_reads[key] >= reads_before_visible:
                    self.records[key] = [record]
                    del self.delayed_records[key]
            records = self.records.get(key, [])
            return 200, json.dumps({
                "success": True,
                "result": records,
                "result_info": {"total_pages": 1},
            }).encode()

        if "/dns_records/" in parsed.path and method == "PATCH":
            record_id = parsed.path.rsplit("/", 1)[1]
            self.patches.append((record_id, payload))
            for records in self.records.values():
                for record in records:
                    if record.get("id") == record_id:
                        record["comment"] = payload["comment"]
            return 200, json.dumps({"success": True, "result": {}}).encode()

        raise AssertionError("fake Cloudflare API received an unexpected request")


def estate(adopt=False, provider="cloudflare"):
    return {
        "name": "alpha",
        "domain": DOMAIN,
        "dns_challenge": {"provider": "cloudflare", "api_token": TOKEN},
        "public_dns": {"enabled": True, "provider": provider, "adopt_existing": adopt},
    }


def static_config(address=ADDRESS, allow_private=False):
    return {
        "ttl": 300,
        "families": ["ipv4"],
        "allow_private": allow_private,
        "address": {"source": "static", "address": address},
    }


def add_record(server, record_type, owner, value, *, comment=None, record_id="record-1"):
    server.records[(record_type, owner)] = [
        {
            "id": record_id,
            "type": record_type,
            "name": owner,
            "content": value,
            "comment": comment,
        }
    ]


def run_preflight(config, server=None, estate_config=None, echo_fetcher=None):
    server = server or FakeCloudflare()
    return public_dns.preflight(
        config,
        [estate() if estate_config is None else estate_config],
        requester=server.request,
        echo_fetcher=echo_fetcher,
        api_base=API_BASE,
    )


def expect_error(action, label):
    try:
        action()
    except public_dns.PublicDNSPreflightError as error:
        return str(error)
    raise AssertionError(f"{label}: expected a safe preflight failure")


def test_lookup_agrees_and_disagrees():
    config = {
        "families": ["ipv4"],
        "allow_private": False,
        "address": {
            "source": "lookup",
            "urls": ["https://echo-one.example.test/ip", "https://echo-two.example.test/ip"],
        },
    }
    calls = []

    def agree(url, family):
        calls.append((url, family))
        return 200, ADDRESS.encode()

    result = run_preflight(config, echo_fetcher=agree)
    assert len(calls) == 2
    assert len(result["records_to_mark"]) == 2

    calls.clear()

    def disagree(url, family):
        calls.append((url, family))
        value = ADDRESS if "one" in url else OTHER_ADDRESS
        return 200, value.encode()

    message = expect_error(
        lambda: run_preflight(config, echo_fetcher=disagree), "disagreeing lookup"
    )
    assert len(calls) == 2
    assert "endpoints disagree for IPv4" in message
    assert ADDRESS not in message and OTHER_ADDRESS not in message


def test_lookup_private_address_override():
    config = {
        "families": ["ipv4"],
        "allow_private": False,
        "address": {
            "source": "lookup",
            "urls": ["https://echo-one.example.test/ip", "https://echo-two.example.test/ip"],
        },
    }
    private_echo = lambda _url, _family: (200, b"192.168.1.10")
    message = expect_error(
        lambda: run_preflight(config, echo_fetcher=private_echo), "private lookup"
    )
    assert "private, loopback, link-local, or CGNAT" in message
    assert "192.168.1.10" not in message

    config["allow_private"] = True
    result = run_preflight(config, echo_fetcher=private_echo)
    assert result["private_override_used"] is True

    static_private = static_config("192.168.1.10", allow_private=False)
    message = expect_error(
        lambda: run_preflight(static_private), "private static address"
    )
    assert "private, loopback, link-local, or CGNAT" in message
    assert "192.168.1.10" not in message
    static_private["allow_private"] = True
    expected, override = public_dns._desired_values(static_private)
    assert expected["A"].compressed == "192.168.1.10"
    assert override is True


def test_absent_equal_different_wrong_type_and_adopt():
    server = FakeCloudflare()
    result = run_preflight(static_config(), server=server)
    expected_names = {DOMAIN, "*." + DOMAIN}
    assert {row["name"] for row in result["records_to_mark"]} == expected_names
    assert {row["type"] for row in result["records_to_mark"]} == {"A"}
    assert sum(method == "GET" for _url, method in server.calls) == 7

    server = FakeCloudflare()
    for owner in expected_names:
        add_record(server, "A", owner, ADDRESS, record_id=f"equal-{owner}")
    result = run_preflight(static_config(), server=server)
    assert result["records_to_mark"] == []

    server = FakeCloudflare()
    add_record(server, "A", DOMAIN, OTHER_ADDRESS)
    message = expect_error(
        lambda: run_preflight(static_config(), server=server), "different record"
    )
    assert "different A record" in message and DOMAIN in message
    assert "record value redacted" in message
    assert OTHER_ADDRESS not in message and TOKEN not in message

    server = FakeCloudflare()
    add_record(server, "CNAME", DOMAIN, "existing.operator.example.net")
    message = expect_error(
        lambda: run_preflight(
            static_config(), server=server, estate_config=estate(adopt=True)
        ),
        "CNAME where address is wanted",
    )
    assert "CNAME record" in message and DOMAIN in message
    assert "cannot be adopted" in message
    assert "existing.operator.example.net" not in message and TOKEN not in message

    server = FakeCloudflare()
    add_record(server, "A", DOMAIN, OTHER_ADDRESS)
    result = run_preflight(
        static_config(), server=server, estate_config=estate(adopt=True)
    )
    assert result["records_to_mark"] == [
        {"estate": "alpha", "name": "*." + DOMAIN, "type": "A"}
    ]

    server = FakeCloudflare()
    add_record(
        server,
        "A",
        DOMAIN,
        OTHER_ADDRESS,
        comment=public_dns.OWNERSHIP_COMMENT,
    )
    result = run_preflight(static_config(), server=server)
    assert result["records_to_mark"] == [
        {"estate": "alpha", "name": "*." + DOMAIN, "type": "A"}
    ]


def test_cname_wrong_address_type_and_adoption():
    config = {
        "ttl": 300,
        "families": ["ipv4"],
        "allow_private": False,
        "address": {"source": "cname", "target": "ddns.operator.example.net"},
    }
    server = FakeCloudflare()
    add_record(server, "A", DOMAIN, ADDRESS)
    message = expect_error(
        lambda: run_preflight(config, server=server, estate_config=estate(adopt=True)),
        "address where CNAME is wanted",
    )
    assert "A record" in message and "CNAME is required" in message
    assert ADDRESS not in message and TOKEN not in message

    server = FakeCloudflare()
    add_record(server, "CNAME", DOMAIN, "foreign.operator.example.net")
    message = expect_error(
        lambda: run_preflight(config, server=server), "different CNAME"
    )
    assert "different CNAME record" in message and DOMAIN in message
    assert "foreign.operator.example.net" not in message

    server = FakeCloudflare()
    add_record(server, "CNAME", DOMAIN, "foreign.operator.example.net")
    result = run_preflight(config, server=server, estate_config=estate(adopt=True))
    assert result["records_to_mark"] == [
        {"estate": "alpha", "name": "*." + DOMAIN, "type": "CNAME"}
    ]


def test_provider_read_failures_are_scrubbed():
    for kind, expected in (
        ("auth", "could not authenticate"),
        ("transport", "could not reach"),
        ("malformed", "malformed response"),
    ):
        server = FakeCloudflare(failure=kind)
        message = expect_error(
            lambda: run_preflight(static_config(), server=server), kind
        )
        assert expected in message
        assert TOKEN not in message
        assert ADDRESS not in message and OTHER_ADDRESS not in message


def test_cloudflare_marks_only_preflight_absences():
    server = FakeCloudflare()
    result = run_preflight(static_config(), server=server)
    candidates = result["records_to_mark"]
    for index, candidate in enumerate(candidates):
        add_record(
            server,
            candidate["type"],
            candidate["name"],
            ADDRESS,
            record_id=f"new-{index}",
        )
    marked = public_dns.mark_created_records(
        static_config(),
        [estate()],
        candidates,
        requester=server.request,
        api_base=API_BASE,
    )
    assert marked == 2
    assert len(server.patches) == 2
    assert all(payload == {"comment": public_dns.OWNERSHIP_COMMENT}
               for _record_id, payload in server.patches)
    assert all(
        record["comment"] == public_dns.OWNERSHIP_COMMENT
        for records in server.records.values()
        for record in records
    )

    equal_server = FakeCloudflare()
    for owner in (DOMAIN, "*." + DOMAIN):
        add_record(equal_server, "A", owner, ADDRESS)
    equal = run_preflight(static_config(), server=equal_server)
    assert equal["records_to_mark"] == []
    assert public_dns.mark_created_records(
        static_config(),
        [estate()],
        equal["records_to_mark"],
        requester=equal_server.request,
        api_base=API_BASE,
    ) == 0
    assert equal_server.patches == []


def test_cloudflare_mark_waits_for_async_records_and_times_out_safely():
    server = FakeCloudflare()
    preflight_result = run_preflight(static_config(), server=server)
    candidates = preflight_result["records_to_mark"]
    for index, candidate in enumerate(candidates):
        server.delay_record(
            candidate["type"],
            candidate["name"],
            ADDRESS,
            reads_before_visible=2,
            record_id=f"delayed-{index}",
        )

    sleeps = []
    marked = public_dns.mark_created_records(
        static_config(),
        [estate()],
        candidates,
        requester=server.request,
        api_base=API_BASE,
        attempts=3,
        retry_delay=5,
        sleep_fn=sleeps.append,
    )
    assert marked == 2
    assert sleeps == [5]
    assert len(server.patches) == 2
    assert all(
        record["comment"] == public_dns.OWNERSHIP_COMMENT
        for records in server.records.values()
        for record in records
    )

    missing_server = FakeCloudflare()
    missing_preflight = run_preflight(static_config(), server=missing_server)
    missing_sleeps = []
    message = expect_error(
        lambda: public_dns.mark_created_records(
            static_config(),
            [estate()],
            missing_preflight["records_to_mark"],
            requester=missing_server.request,
            api_base=API_BASE,
            attempts=3,
            retry_delay=5,
            sleep_fn=missing_sleeps.append,
        ),
        "records not created before timeout",
    )
    assert "did not return all newly published DNS records" in message
    assert TOKEN not in message
    assert ADDRESS not in message and OTHER_ADDRESS not in message
    assert missing_sleeps == [5, 5]
    assert missing_server.patches == []


def test_role_preflights_before_publication_and_marks_after():
    tasks = yaml.safe_load((repo / "ansible/roles/caddy/tasks/main.yml").read_text())
    names = [task.get("name") for task in tasks]
    preflight_index = names.index("Preflight public DNS addresses and existing provider records")
    install_index = names.index("Install Caddy")
    mark_index = names.index("Mark newly created Cloudflare public DNS records")
    reconcile_index = names.index("Reconcile the Caddy dynamic DNS app")
    assert preflight_index < install_index < reconcile_index < mark_index
    assert tasks[preflight_index]["no_log"] is True
    assert tasks[mark_index]["no_log"] is True
    assert names.index("Refuse unsafe public DNS preflight results") > preflight_index


def main():
    test_lookup_agrees_and_disagrees()
    test_lookup_private_address_override()
    test_absent_equal_different_wrong_type_and_adopt()
    test_cname_wrong_address_type_and_adoption()
    test_provider_read_failures_are_scrubbed()
    test_cloudflare_marks_only_preflight_absences()
    test_cloudflare_mark_waits_for_async_records_and_times_out_safely()
    test_role_preflights_before_publication_and_marks_after()
    print("Public DNS preflight: fake lookup and Cloudflare API checks passed")


if __name__ == "__main__":
    main()
