#!/usr/bin/env python3
"""Render production templates with Ansible's templar."""

import json
import os
from pathlib import Path
import sys
import urllib.parse
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
DEFAULTS_FILE = ROOT / "ansible/vars/app-defaults/caddy.yml"
ROLE_FILE = ROOT / "ansible/roles/caddy/tasks/main.yml"
STAGING_DIRECTORY = "https://acme-staging-v02.api.letsencrypt.org/directory"


def render(expression, variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


class CaddyRendering:

    def setup(self):
        self.defaults = yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8"))[
            "caddy_defaults"
        ]
        self.tasks = yaml.safe_load(ROLE_FILE.read_text(encoding="utf-8"))
        self.public_dns_tasks = yaml.safe_load(
            (ROOT / "ansible/roles/caddy/tasks/public-dns-contract.yml").read_text(
                encoding="utf-8"
            )
        )
        self.policy_task = task_named(
            self.tasks, "Build per-estate DNS-challenge TLS policies"
        )
        self.base_task = task_named(self.tasks, "Build the desired base configuration")
        self.write_task = task_named(self.tasks, "Write the base configuration")
        self.policy_expression = self.policy_task["ansible.builtin.set_fact"][
            "_caddy_dns_policies"
        ]
        self.base_expression = self.base_task["ansible.builtin.set_fact"][
            "_caddy_base_config"
        ]
        self.resolver_task = task_named(
            self.tasks, "Collect DNS-challenge resolver defaults"
        )
        self.validation_task = task_named(
            self.tasks, "Validate DNS-challenge resolver path"
        )
        self.collect_task = task_named(self.tasks, "Collect DNS-challenge domains")
        self.policy_vars = self.policy_task["vars"]
        self.base_vars = self.base_task["vars"]
        self.content_expression = self.write_task["ansible.builtin.copy"]["content"]
        self.public_dns_input_task = task_named(
            self.public_dns_tasks, "Read lab-wide public DNS input"
        )
        self.public_dns_resolve_task = task_named(
            self.public_dns_tasks, "Resolve public DNS config for each estate"
        )
        self.public_dns_apps_task = task_named(
            self.public_dns_tasks, "Build public DNS Caddy app configurations"
        )
        self.estates = [
            {
                "domain": "alpha.example.test",
                "dns_challenge": {
                    "provider": "cloudflare",
                    "api_token": "fixture-token-alpha",
                    "propagation_delay": "45s",
                },
            },
            {
                "domain": "beta.example.test",
                "dns_challenge": {
                    "provider": "cloudflare",
                    "api_token": "fixture-token-beta",
                },
            },
        ]

    def default_resolvers(self, facts=None):
        return render(
            self.resolver_task["ansible.builtin.set_fact"]["_caddy_default_resolvers"],
            {
                "ansible_facts": (
                    facts
                    if facts is not None
                    else {"dns": {"nameservers": ["192.0.2.53", "2001:db8::53"]}}
                )
            },
        )

    def collect_domains(self, infra, legacy):
        variables = {
            "homelabinfra_config": {"infrastructure": infra},
            "app_config": {"app": {"dns_challenge": legacy}},
        }
        for key, expression in self.collect_task["vars"].items():
            variables[key] = render(expression, variables)
        return render(
            self.collect_task["ansible.builtin.set_fact"]["_caddy_dns_domains"],
            variables,
        )

    def resolve_public_dns(self, infrastructure):
        input_task = self.public_dns_input_task
        variables = {"homelabinfra_config": {"infrastructure": infrastructure}}
        for key, expression in input_task["ansible.builtin.set_fact"].items():
            variables[key] = render(expression, variables)
        task = self.public_dns_resolve_task
        for key, expression in task["vars"].items():
            variables[key] = render(expression, variables)
        for key, expression in task["ansible.builtin.set_fact"].items():
            variables[key] = render(expression, variables)
        return variables

    def build_public_dns_apps(self, infrastructure):
        variables = self.resolve_public_dns(infrastructure)
        task = self.public_dns_apps_task
        for key, expression in task["vars"].items():
            variables[key] = render(expression, variables)
        for key, expression in task["ansible.builtin.set_fact"].items():
            variables[key] = render(expression, variables)
        return variables

    def resolvers_valid(self, challenge, facts=None):
        variables = {
            "_caddy_dns_domains": [
                {
                    "dns_challenge": {
                        "api_token": "fixture-token-validation",
                        **challenge,
                    }
                }
            ],
            "_caddy_default_resolvers": self.default_resolvers(facts),
        }
        loop = render(self.validation_task["loop"], variables)
        assert len(loop) == 1, "len(loop) == 1"
        assert "fixture-token-validation" not in str(
            loop
        ), "'fixture-token-validation' not in str(loop)"
        variables["item"] = loop[0]
        for key, expression in self.validation_task["vars"].items():
            variables[key] = render(expression, variables)
        return all(
            (
                render("{{ " + condition + " }}", variables)
                for condition in self.validation_task["ansible.builtin.assert"]["that"]
            )
        )

    def render_policies(
        self, app: dict[str, object], *, estates=None, facts=None
    ) -> list[dict[str, object]]:
        policies: list[dict[str, object]] = []
        for estate in self.estates if estates is None else estates:
            variables: dict[str, object] = {
                "app_config": {"app": app},
                "item": estate,
                "_caddy_dns_policies": policies,
                "_caddy_default_resolvers": self.default_resolvers(facts),
            }
            for key, expression in self.policy_vars.items():
                variables[key] = (
                    render(expression, variables)
                    if isinstance(expression, str) and "{{" in expression
                    else expression
                )
            policies = render(self.policy_expression, variables)
        return policies

    def render_config(self, app: dict[str, object], *, public_dns_apps=None) -> str:
        policies = self.render_policies(app)
        variables: dict[str, object] = {
            "app_config": {"app": app},
            "_caddy_dns_policies": policies,
            "_caddy_automate_names": ["*.alpha.example.test", "*.beta.example.test"],
            "_caddy_bind_ip": "192.0.2.10",
            "_caddy_public_dns_apps": public_dns_apps or {},
        }
        for key, expression in self.base_vars.items():
            variables[key] = render(expression, variables)
        variables["_caddy_base_config"] = render(self.base_expression, variables)
        return render(self.content_expression, variables)

    def app_config(self, *, ca: str = "", tls_mode: str = "acme") -> dict[str, object]:
        return {
            "port": 2019,
            "http_port": 80,
            "https_port": 443,
            "tls_mode": tls_mode,
            "acme_email": "ops@example.test",
            "acme_ca": ca,
        }


def caddy_default_empty_acme_ca_renders_guest_dns_and_preserves_other_config(self):
    output = self.render_config(self.app_config())
    expected = {
        "admin": {"listen": "192.0.2.10:2019"},
        "apps": {
            "http": {"servers": {"srv0": {"listen": [":443", ":80"], "routes": []}}},
            "tls": {
                "automation": {
                    "policies": [
                        {
                            "subjects": ["*.alpha.example.test", "alpha.example.test"],
                            "issuers": [
                                {
                                    "module": "acme",
                                    "challenges": {
                                        "dns": {
                                            "provider": {
                                                "name": "cloudflare",
                                                "api_token": "fixture-token-alpha",
                                            },
                                            "resolvers": ["192.0.2.53", "2001:db8::53"],
                                            "propagation_delay": "45s",
                                            "propagation_timeout": -1,
                                        }
                                    },
                                    "email": "ops@example.test",
                                }
                            ],
                        },
                        {
                            "subjects": ["*.beta.example.test", "beta.example.test"],
                            "issuers": [
                                {
                                    "module": "acme",
                                    "challenges": {
                                        "dns": {
                                            "provider": {
                                                "name": "cloudflare",
                                                "api_token": "fixture-token-beta",
                                            },
                                            "resolvers": ["192.0.2.53", "2001:db8::53"],
                                            "propagation_delay": "30s",
                                            "propagation_timeout": -1,
                                        }
                                    },
                                    "email": "ops@example.test",
                                }
                            ],
                        },
                        {"issuers": [{"module": "acme", "email": "ops@example.test"}]},
                    ]
                },
                "certificates": {
                    "automate": ["*.alpha.example.test", "*.beta.example.test"]
                },
            },
        },
    }
    expected_bytes = render("{{ baseline | to_nice_json }}", {"baseline": expected})
    assert output == expected_bytes, "output == expected_bytes"


def caddy_configured_ca_reaches_every_acme_issuer_and_keeps_email(self):
    config = json.loads(self.render_config(self.app_config(ca=STAGING_DIRECTORY)))
    policies = config["apps"]["tls"]["automation"]["policies"]
    for policy in policies:
        issuer = policy["issuers"][0]
        assert issuer["module"] == "acme", "issuer['module'] == 'acme'"
        assert issuer["ca"] == STAGING_DIRECTORY, "issuer['ca'] == STAGING_DIRECTORY"
        assert (
            issuer["email"] == "ops@example.test"
        ), "issuer['email'] == 'ops@example.test'"


def caddy_internal_mode_ignores_configured_ca(self):
    internal_bytes = self.render_config(
        self.app_config(ca=STAGING_DIRECTORY, tls_mode="internal")
    )
    same_internal_without_ca = self.render_config(self.app_config(tls_mode="internal"))
    assert (
        internal_bytes == same_internal_without_ca
    ), "internal_bytes == same_internal_without_ca"
    internal = json.loads(internal_bytes)
    policies = internal["apps"]["tls"]["automation"]["policies"]
    for policy in policies[:-1]:
        issuer = policy["issuers"][0]
        assert "ca" not in issuer, "'ca' not in issuer"
        assert (
            issuer["email"] == "ops@example.test"
        ), "issuer['email'] == 'ops@example.test'"
    assert policies[-1]["issuers"] == [
        {"module": "internal"}
    ], "policies[-1]['issuers'] == [{'module': 'internal'}]"


def caddy_declared_lab_dns_reaches_guest_and_disabled_propagation_policy(self):
    tasks = yaml.safe_load(
        (ROOT / "ansible/tasks/network/resolve-network.yml").read_text()
    )
    expression = task_named(
        tasks, "Resolve network | Build the effective config for that network"
    )["ansible.builtin.set_fact"]["network_effective"]
    config = {
        "networks": {
            "default": {"dns_servers": ["192.0.2.53"], "searchdomain": "example.test"},
            "shared": {"ip_offset": 10},
        }
    }
    for shared in ({"ip_offset": 10}, {"dns_servers": ["192.0.2.54", "2001:db8::54"]}):
        config["networks"]["shared"] = shared
        network = render(
            expression, {"homelabinfra_config": config, "network_selected": "shared"}
        )
        create_tasks = yaml.safe_load(
            (ROOT / "ansible/tasks/proxmox/lxc-create.yml").read_text()
        )
        module_task = task_named(create_tasks, "Merge LXC network settings")
        guest = render(
            module_task["ansible.builtin.set_fact"]["homelabinfra_instance"],
            {
                "homelabinfra_instance": {"network": network, "lxc": {"memory": 512}},
                "lxc_netif": "name=eth0,bridge=vmbr0,ip=192.0.2.10/24",
            },
        )
        assert guest["lxc"]["memory"] == 512, "guest['lxc']['memory'] == 512"
        nameservers = guest["lxc"]["nameserver"].split()
        assert (
            nameservers == network["dns_servers"]
        ), "nameservers == network['dns_servers']"
        facts = {"dns": {"nameservers": nameservers}}
        for policy in self.render_policies(
            self.app_config(ca=STAGING_DIRECTORY), facts=facts
        ):
            dns = policy["issuers"][0]["challenges"]["dns"]
            assert dns["resolvers"] == nameservers, "dns['resolvers'] == nameservers"
            assert dns["propagation_timeout"] == -1, "dns['propagation_timeout'] == -1"
            assert (
                policy["issuers"][0]["ca"] == STAGING_DIRECTORY
            ), "policy['issuers'][0]['ca'] == STAGING_DIRECTORY"


def caddy_public_dns_renders_each_address_source_and_provider(self):
    alpha = {
        "domain": "alpha.example.test",
        "dns_challenge": {"provider": "cloudflare", "api_token": "fixture-token-alpha"},
        "public_dns": {"enabled": True},
    }
    beta = {
        "domain": "beta.example.test",
        "dns_challenge": {
            "provider": "route53",
            "access_key_id": "fixture-access-id-beta",
            "secret_access_key": "fixture-secret-key-beta",
            "region": "fixture-region",
        },
        "public_dns": {"enabled": True, "provider": "route53"},
    }
    gamma = {"domain": "gamma.example.test", "public_dns": {"enabled": False}}
    infra = {
        "public_dns": {
            "ttl": 180,
            "families": ["ipv4"],
            "allow_private": False,
            "address": {
                "source": "lookup",
                "urls": ["https://one.example.test/ip", "https://two.example.test/ip"],
            },
        },
        "domains": {"alpha": alpha, "beta": beta, "gamma": gamma},
    }
    rendered = self.build_public_dns_apps(infra)
    apps = rendered["_caddy_public_dns_apps"]
    expected_providers = [
        {
            "dns_provider": {"name": "cloudflare", "api_token": "fixture-token-alpha"},
            "domains": {"alpha.example.test": ["@", "*"]},
        },
        {
            "dns_provider": {
                "name": "route53",
                "access_key_id": "fixture-access-id-beta",
                "secret_access_key": "fixture-secret-key-beta",
                "region": "fixture-region",
            },
            "domains": {"beta.example.test": ["@", "*"]},
        },
    ]
    assert rendered["_caddy_public_dns_config"] == infra["public_dns"], (
        "the lab-wide public_dns settings are preserved"
    )
    assert [estate["name"] for estate in rendered["_caddy_public_dns_enabled_estates"]] == [
        "alpha", "beta"
    ], "only opted-in estates are enabled"
    assert rendered["_caddy_public_dns_enabled_estates"][1]["public_dns"] == beta["public_dns"], (
        "the enabled estate settings are preserved for app rendering"
    )
    assert apps["dynamic_dns"] == {
        "providers": expected_providers,
        "ip_sources": [{
            "source": "simple_http",
            "endpoints": ["https://one.example.test/ip", "https://two.example.test/ip"],
        }],
        "versions": {"ipv4": True, "ipv6": False},
        "ttl": "180s",
        "exclude": [
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
            "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10",
            "::1/128", "fc00::/7", "fe80::/10",
        ],
    }, "lookup app has expected providers, source, families, TTL and exclusions"
    base = json.loads(self.render_config(self.app_config(), public_dns_apps=apps))
    assert base["apps"]["dynamic_dns"] == apps["dynamic_dns"], (
        "the base Caddy JSON contains the dynamic DNS app"
    )
    assert self.build_public_dns_apps(infra)["_caddy_public_dns_apps"] == apps, (
        "a second public DNS render is identical"
    )

    inherited_default = self.build_public_dns_apps(
        {
            "reverse_proxy": {
                "dns_challenge": {
                    "provider": "cloudflare",
                    "api_token": "fixture-default-token",
                }
            },
            "public_dns": {
                "address": {"source": "static", "address": "203.0.113.10"},
            },
            "domains": {
                "beta": {
                    "domain": "beta.example.test",
                    "public_dns": {"enabled": False},
                },
                "alpha": {
                    "default": "true",
                    "domain": "alpha.example.test",
                    "public_dns": {"enabled": True},
                },
            },
        }
    )
    assert inherited_default["_caddy_public_dns_apps"]["dynamic_dns"]["providers"] == [
        {
            "dns_provider": {
                "name": "cloudflare",
                "api_token": "fixture-default-token",
            },
            "domains": {"alpha.example.test": ["@", "*"]},
        },
    ], "the default estate inherits the lab's DNS-01 challenge for public DNS"
    beta_inherited = next(
        estate for estate in inherited_default["_caddy_public_dns_estates"]
        if estate["name"] == "beta"
    )
    assert beta_inherited["dns_challenge_provider"] == "" and not beta_inherited["has_dns_challenge_credentials"], (
        "the default estate's DNS-01 credentials do not leak to another estate"
    )

    inherited_with_estate_credentials = self.build_public_dns_apps(
        {
            "reverse_proxy": {
                "dns_challenge": {
                    "provider": "cloudflare",
                    "api_token": "fixture-global-token",
                }
            },
            "public_dns": {
                "address": {"source": "static", "address": "203.0.113.10"},
            },
            "domains": {
                "alpha": {
                    "default": True,
                    "domain": "alpha.example.test",
                    "dns_challenge": {"api_token": "fixture-estate-token"},
                    "public_dns": {"enabled": True},
                },
                "beta": {
                    "domain": "beta.example.test",
                    "public_dns": {"enabled": False},
                },
            },
        }
    )
    assert inherited_with_estate_credentials["_caddy_public_dns_apps"]["dynamic_dns"]["providers"] == [
        {
            "dns_provider": {
                "name": "cloudflare",
                "api_token": "fixture-estate-token",
            },
            "domains": {"alpha.example.test": ["@", "*"]},
        },
    ], "the default estate's Vault credential overlays the inherited DNS-01 challenge"

    static_infra = {
        "domains": {"alpha": alpha},
        "public_dns": {
            "families": ["ipv4", "ipv6"],
            "address": {"source": "static", "address": "203.0.113.10"},
        },
    }
    static_apps = self.build_public_dns_apps(static_infra)["_caddy_public_dns_apps"]
    assert static_apps["dynamic_dns"]["ip_sources"] == [
        {"source": "static", "ips": ["203.0.113.10"]}
    ], "static source uses Caddy's static IP source"
    assert static_apps["dynamic_dns"]["versions"] == {
        "ipv4": True, "ipv6": True
    }, "both requested IP families are enabled"

    cname_infra = {
        "domains": {"alpha": alpha, "beta": beta, "gamma": gamma},
        "public_dns": {
            "ttl": 600,
            "address": {"source": "cname", "target": "dynamic.operator.example.net"},
        },
    }
    cname_apps = self.build_public_dns_apps(cname_infra)["_caddy_public_dns_apps"]
    assert "dynamic_dns" not in cname_apps, "CNAME source has no dynamic updater"
    records = cname_apps["dns_records"]
    assert records["ttl"] == "600s", "CNAME records use the lab-wide TTL"
    assert records["best_effort"] is True, "CNAME provider failures do not stop Caddy"
    assert records["providers"] == [
        {
            "dns_provider": expected_providers[0]["dns_provider"],
            "records": [
                {"name": "alpha.example.test", "zone": "alpha.example.test", "type": "CNAME", "value": ["dynamic.operator.example.net."]},
                {"name": "*.alpha.example.test", "zone": "alpha.example.test", "type": "CNAME", "value": ["dynamic.operator.example.net."]},
            ],
        },
        {
            "dns_provider": expected_providers[1]["dns_provider"],
            "records": [
                {"name": "beta.example.test", "zone": "beta.example.test", "type": "CNAME", "value": ["dynamic.operator.example.net."]},
                {"name": "*.beta.example.test", "zone": "beta.example.test", "type": "CNAME", "value": ["dynamic.operator.example.net."]},
            ],
        },
    ], "CNAME source publishes apex and wildcard for each enabled estate"

    private_apps = self.build_public_dns_apps(
        {
            "domains": {"alpha": alpha},
            "public_dns": {
                "allow_private": True,
                "address": {"source": "static", "address": "192.168.1.10"},
            },
        }
    )["_caddy_public_dns_apps"]
    assert "exclude" not in private_apps["dynamic_dns"], (
        "allow_private removes the global address exclusions"
    )

    disabled = self.build_public_dns_apps(
        {"domains": {"alpha": {"domain": "alpha.example.test", "public_dns": {"enabled": False}}}}
    )
    assert disabled["_caddy_public_dns_apps"] == {}, "disabled estates render no public DNS app"
    assert disabled["_caddy_public_dns_enabled_estates"] == [], "no disabled estate is included"

    for task_name in (
        "Reconcile the Caddy dynamic DNS app",
        "Reconcile CNAME records through the estate DNS providers",
    ):
        task = task_named(self.tasks, task_name)
        assert "_running.status != 200" in task["when"][-1], (
            f"{task_name} compares current and desired app config"
        )

    for running_var, enabled_var, task_name in (
        (
            "_caddy_dynamic_dns_running",
            "_caddy_dynamic_dns_enabled",
            "Remove the Caddy dynamic DNS app when no estate uses it",
        ),
        (
            "_caddy_dns_records_running",
            "_caddy_dns_records_enabled",
            "Remove the Caddy static DNS records app when no estate uses it",
        ),
    ):
        task = task_named(self.tasks, task_name)

        def should_remove(response):
            variables = {running_var: response, enabled_var: False}
            return all(
                bool(render("{{ " + condition + " }}", variables))
                for condition in task["when"]
            )

        assert not should_remove({"status": 200, "json": None}), (
            f"{task_name} skips Caddy's HTTP 200 JSON null for an absent app"
        )
        assert not should_remove({"status": 404, "json": None}), (
            f"{task_name} skips a Caddy 404 response"
        )
        assert should_remove({"status": 200, "json": {"providers": []}}), (
            f"{task_name} removes an existing Caddy app config"
        )


def caddy_explicit_estate_options_are_isolated_and_provider_fields_preserved(self):
    estates = self.collect_domains(
        {
            "domains": {
                "alpha": {
                    "domain": "alpha.example.test",
                    "dns_challenge": {
                        "provider": "route53",
                        "access_key_id": "fixture-access-id",
                        "secret_access_key": "fixture-secret-key",
                        "region": "fixture-region",
                        "resolvers": ["192.0.2.60:5353", "[2001:db8::60]:53"],
                        "propagation_timeout": -1,
                        "propagation_delay": "10s",
                        "ttl": "60s",
                        "override_domain": "validation.example.test",
                    },
                },
                "beta": self.estates[1],
            }
        },
        {"resolvers": ["192.0.2.99:53"]},
    )
    policies = self.render_policies(self.app_config(), estates=estates)
    alpha, beta = [p["issuers"][0]["challenges"]["dns"] for p in policies]
    assert alpha == {
        "provider": {
            "name": "route53",
            "access_key_id": "fixture-access-id",
            "secret_access_key": "fixture-secret-key",
            "region": "fixture-region",
        },
        "resolvers": ["192.0.2.60:5353", "[2001:db8::60]:53"],
        "propagation_timeout": -1,
        "propagation_delay": "10s",
        "ttl": "60s",
        "override_domain": "validation.example.test",
    }, "alpha == {'provider': {'name': 'route53', 'access_key_id': 'fixture-access-id', 'secret_access_key': 'fixture-secret-key', 'region': 'fixture-region'}, 'resolvers': ['192.0.2.60:5353', '[2001:db8::60]:53'], 'propagation_timeout': -1, 'propagation_delay': '10s', 'ttl': '60s', 'override_domain': 'validation.example.test'}"
    assert (
        beta["resolvers"] == self.default_resolvers()
    ), "beta['resolvers'] == self.default_resolvers()"
    assert beta["provider"] == {
        "name": "cloudflare",
        "api_token": "fixture-token-beta",
    }, "beta['provider'] == {'name': 'cloudflare', 'api_token': 'fixture-token-beta'}"
    assert beta["propagation_delay"] == "30s", "beta['propagation_delay'] == '30s'"


def caddy_infrastructure_overrides_legacy_instance_resolvers_only_for_default_policy(
    self,
):
    legacy = {
        "provider": "cloudflare",
        "api_token": "fixture-token-legacy",
        "resolvers": ["192.0.2.61:53"],
        "propagation_delay": "40s",
    }
    infra = {
        "domain": "default.example.test",
        "reverse_proxy": {
            "dns_challenge": {
                "resolvers": ["192.0.2.62:53"],
                "propagation_timeout": "2m",
            }
        },
    }
    for overrides, expected in (
        ({}, legacy["resolvers"]),
        (infra["reverse_proxy"], ["192.0.2.62:53"]),
    ):
        domains = self.collect_domains({**infra, "reverse_proxy": overrides}, legacy)
        dns = self.render_policies(self.app_config(), estates=domains)[0]["issuers"][0][
            "challenges"
        ]["dns"]
        assert dns["resolvers"] == expected, "dns['resolvers'] == expected"
        assert (
            dns["provider"]["api_token"] == "fixture-token-legacy"
        ), "dns['provider']['api_token'] == 'fixture-token-legacy'"
        assert dns["propagation_delay"] == "40s", "dns['propagation_delay'] == '40s'"
        assert dns["propagation_timeout"] == (
            "2m" if overrides else -1
        ), "dns['propagation_timeout'] == ('2m' if overrides else -1)"


def caddy_missing_or_invalid_resolvers_fail_without_external_fallback(self):
    for facts in ({}, {"dns": {"nameservers": []}}):
        assert not self.resolvers_valid(
            {}, facts
        ), "not self.resolvers_valid({}, facts)"
        assert self.resolvers_valid(
            {"resolvers": ["192.0.2.53:53"]}, facts
        ), "self.resolvers_valid({'resolvers': ['192.0.2.53:53']}, facts)"
    assert self.resolvers_valid({}), "self.resolvers_valid({})"
    for invalid in ([], "192.0.2.53", {}, None, [None], [""]):
        assert not self.resolvers_valid(
            {"resolvers": invalid}
        ), "not self.resolvers_valid({'resolvers': invalid})"


def emby_rendering():
    compose_template = read("ansible/roles/emby/templates/docker-compose.yml.j2")
    defaults = yaml.safe_load(read("ansible/vars/app-defaults/emby.yml"))
    assert defaults["emby_defaults"]["app"]["gpu_device"] == "", (
        "Emby's default GPU device remains empty"
    )
    compose = render(
        compose_template,
        {
            "instance": "emby-fixture",
            "app_config": {
                "app": {
                    "image": "lscr.io/linuxserver/emby:latest",
                    "config_path": "/opt/emby-fixture/config",
                    "port": 18096,
                    "container_port": 8096,
                    "puid": 1313,
                    "pgid": 1313,
                    "devices": [],
                }
            },
            "homelabinfra_config": {"timezone": "UTC"},
            "_emby_mounts": [
                {"path": "/srv/fixtures/media"},
                {"path": "/srv/fixtures/music"},
            ],
        },
    )
    assert (
        '"/opt/emby-fixture/config:/config"' in compose
    ), "'\"/opt/emby-fixture/config:/config\"' in compose"
    assert (
        '"/srv/fixtures/media:/srv/fixtures/media:ro"' in compose
    ), "'\"/srv/fixtures/media:/srv/fixtures/media:ro\"' in compose"
    assert (
        '"/srv/fixtures/music:/srv/fixtures/music:ro"' in compose
    ), "'\"/srv/fixtures/music:/srv/fixtures/music:ro\"' in compose"
    assert '"18096:8096"' in compose, "'\"18096:8096\"' in compose"
    assert ":/config:ro" not in compose, "':/config:ro' not in compose"
    role_tasks = yaml.safe_load(read("ansible/roles/emby/tasks/main.yml"))
    identity_wait = task_named(
        role_tasks, "Wait for Emby's public system-info endpoint"
    )
    identity_guard = task_named(
        role_tasks, "Assert Emby returns its public system information"
    )
    identity_conditions = identity_wait["until"] + identity_guard[
        "ansible.builtin.assert"
    ]["that"]
    assert all("ProductName" not in condition for condition in identity_conditions), (
        "Emby's public system-info response does not define ProductName"
    )

    def has_public_system_info(response):
        variables = {"_emby_public": response}
        return all(
            render("{{ " + condition + " }}", variables)
            for condition in identity_conditions
        )

    assert has_public_system_info(
        {
            "status": 200,
            "json": {
                "Id": "emby-server-id",
                "ServerName": "Emby Server",
                "Version": "4.10.1.0",
            },
        }
    ), "documented Emby public system-info fields"
    assert not has_public_system_info(
        {"status": 200, "json": {"ProductName": "Emby Server"}}
    ), "incomplete public system-info response"

    restore_tasks = yaml.safe_load(read("ansible/roles/emby/tasks/restore.yml"))
    restore_block = task_named(
        restore_tasks, "Emby restore | Replace the application config from PBS"
    )["block"]
    restore_health = task_named(
        restore_block, "Emby restore | Wait for the restored service"
    )
    restore_identity = task_named(
        restore_block, "Emby restore | Assert the restored service is Emby"
    )
    restore_extract = task_named(
        restore_block, "Emby restore | Extract the selected archive"
    )
    restore_extract_argv = restore_extract["ansible.builtin.command"]["argv"]
    assert any(
        restore_extract_argv[index : index + 2] == ["--tmpfs", "/tmp"]
        for index in range(len(restore_extract_argv) - 1)
    ), "Emby PBS extraction gets a writable temporary filesystem"
    restore_identity_conditions = restore_health["until"] + restore_identity[
        "ansible.builtin.assert"
    ]["that"]
    assert all("ProductName" not in condition for condition in restore_identity_conditions), (
        "Emby restore validates documented public system-info fields"
    )

    def has_restore_system_info(response):
        variables = {"_emby_restore_health": response}
        return all(
            render("{{ " + condition + " }}", variables)
            for condition in restore_identity_conditions
        )

    assert has_restore_system_info(
        {
            "status": 200,
            "json": {
                "Id": "emby-server-id",
                "ServerName": "Emby Server",
                "Version": "4.10.1.0",
            },
        }
    ), "restored Emby public system-info response"
    assert not has_restore_system_info(
        {"status": 200, "json": {"ProductName": "Emby Server"}}
    ), "restore rejects an unsupported Emby info field"

    wizard_tasks = yaml.safe_load(read("ansible/roles/emby/tasks/setup-wizard.yml"))
    setup_state = task_named(
        role_tasks, "Read Emby's unauthenticated startup endpoint state"
    )
    assert setup_state["ansible.builtin.uri"]["url"] == (
        "{{ _emby_base }}/Startup/User"
    ), "deploy detects first-run state through Emby's startup endpoint"
    assert setup_state["ansible.builtin.uri"]["status_code"] == [200, 401], (
        "the startup endpoint is public during setup and protected after completion"
    )
    assert setup_state["no_log"] is True, "startup endpoint response stays private"
    wizard_include = task_named(role_tasks, "Complete Emby's first-run wizard")
    assert wizard_include["when"] == "_emby_setup_state.status == 200", (
        "only an unauthenticated startup endpoint runs the setup wizard"
    )
    assert "StartupWizardCompleted" not in read(
        "ansible/roles/emby/tasks/main.yml"
    ) + read("ansible/roles/emby/tasks/setup-wizard.yml"), (
        "Emby public system info does not expose StartupWizardCompleted"
    )

    startup_user_read = task_named(
        wizard_tasks, "First run | Read the existing Emby administrator account"
    )
    assert startup_user_read["ansible.builtin.uri"]["url"] == (
        "{{ _emby_base }}/Startup/User"
    ), "first-run username comes from Emby's Startup/User endpoint"
    assert startup_user_read["no_log"] is True, "startup account details stay private"
    startup_user_set = task_named(
        wizard_tasks, "First run | Use Emby's existing administrator account"
    )
    assert "_emby_startup_user.json.Name" in str(
        startup_user_set["ansible.builtin.set_fact"]["_emby_user"]
    ), "the canonical login uses Emby's existing first account"
    assert not any(
        task.get("ansible.builtin.uri", {}).get("url") == "{{ _emby_base }}/Startup/User"
        and task.get("ansible.builtin.uri", {}).get("method", "GET") == "POST"
        for task in wizard_tasks
    ), "the passwordless Emby account is not renamed during first-run setup"
    setup_closed = task_named(
        wizard_tasks,
        "First run | Confirm Emby's startup endpoint now requires authentication",
    )
    assert setup_closed["until"] == "_emby_setup_closed.status == 401", (
        "first-run completion is verified with the protected startup endpoint"
    )
    password_setup = yaml.safe_load(
        read("ansible/roles/emby/tasks/set-admin-password.yml")
    )
    password_auth = task_named(
        password_setup,
        "Emby | Authenticate the initial administrator without a password",
    )
    assert password_auth["ansible.builtin.uri"]["body"]["Pw"] == "", (
        "recovery authenticates only an initial passwordless administrator"
    )
    password_set = task_named(
        password_setup,
        "Emby | Set the app-owned administrator password through its API",
    )
    assert password_set["ansible.builtin.uri"]["body"]["ResetPassword"] is False, (
        "setting the new password does not invoke Emby's password-clear operation"
    )
    assert password_set["ansible.builtin.uri"]["body"]["CurrentPw"] == "", (
        "first-run password setting authenticates the passwordless account"
    )
    assert task_named(
        role_tasks,
        "Recover Emby's passwordless administrator after interrupted first run",
    )["when"] == "_emby_auth_attempt.status == 401", (
        "passwordless recovery is attempted only when canonical credentials fail"
    )

    mount_check = task_named(
        role_tasks,
        "Resolve whether each Emby library path is under a declared media mount",
    )
    mount_guard = next(
        (
            task
            for task in role_tasks
            if task.get("name") == "Assert each Emby library is inside a declared media mount"
        )
    )
    mount_result_template = mount_check["vars"]["_emby_library_mount_result"]
    mount_fact_template = mount_check["ansible.builtin.set_fact"][
        "_emby_library_mount_valid"
    ]
    mount_assertions = mount_guard["ansible.builtin.assert"]["that"]
    assert "{%" not in str(mount_assertions) and "{{" not in str(mount_assertions), (
        "the mount guard must evaluate a typed fact, not a Jinja control block"
    )

    def mount_is_valid(mounts):
        item = {"_root": "/srv/fixtures/media", "subpath": "movies"}
        rendered = render(
            mount_result_template,
            {"item": item, "_emby_mounts": mounts},
        )
        fact = render(
            mount_fact_template,
            {"_emby_library_mount_result": rendered},
        )
        assert isinstance(fact, bool), f"mount membership must be typed, got {fact!r}"
        condition = render(
            "{{ " + mount_assertions[0] + " }}",
            {
                "item": {
                    "ansible_facts": {
                        "_emby_library_mount_valid": fact,
                    }
                }
            },
        )
        assert isinstance(condition, bool), f"assert input must stay typed, got {condition!r}"
        return condition

    assert mount_is_valid([{"path": "/srv/fixtures/media"}]), "declared parent mount"
    assert mount_is_valid([{"path": "/srv/fixtures"}]), "nested library under parent mount"
    assert not mount_is_valid([{"path": "/srv/fixtures/other"}]), "unmounted library"
    assert not mount_is_valid([{"path": "/srv/fixtures/mediasomething"}]), (
        "a shared prefix must not count as a containing mount"
    )


def unpackerr_rendering():
    defaults = yaml.safe_load(read("ansible/vars/app-defaults/unpackerr.yml"))[
        "unpackerr_defaults"
    ]
    template = read("ansible/roles/unpackerr/templates/docker-compose.yml.j2")
    arrs = sorted(
        [
            {
                "key": name,
                "value": {
                    "app": app,
                    "host": f"http://{name}:8989",
                    "api_key": f"fixture-{name}-key",
                },
            }
            for name, app in (
                ("sonarr", "sonarr"),
                ("sonarr-anime", "sonarr"),
                ("radarr", "radarr"),
                ("lidarr", "lidarr"),
            )
        ],
        key=lambda entry: entry["key"],
    )

    def compose(parallel):
        app = dict(defaults["app"])
        if parallel is not None:
            app["parallel"] = parallel
        app.update(
            {
                "image": "golift/unpackerr:latest",
                "download_path": "/srv/fixtures/downloads",
            }
        )
        return render(
            template,
            {
                "instance": "unpackerr-fixture",
                "app_config": {"app": app},
                "homelabinfra_config": {"timezone": "UTC"},
                "_unpackerr_arrs": arrs,
                "_unpackerr_mounts": [{"path": "/srv/fixtures"}],
            },
        )

    for configured, expected in ((None, 1), (3, 3)):
        output = compose(configured)
        assert output.count(f"UN_PARALLEL={expected}") == 1, "parallel setting rendered once"
        app_indexes = {}
        for entry in arrs:
            app = entry["value"]["app"]
            index = app_indexes.get(app, 0)
            app_indexes[app] = index + 1
            prefix = f"UN_{app.upper()}_{index}_"
            assert (
                output.count(f"{prefix}URL={entry['value']['host']}") == 1
            ), "Arr URL rendered once"
            assert (
                output.count(f"{prefix}API_KEY={entry['value']['api_key']}") == 1
            ), "Arr key rendered once"


def navidrome_rendering():
    compose_template = read("ansible/roles/navidrome/templates/docker-compose.yml.j2")
    compose = render(
        compose_template,
        {
            "instance": "navidrome-fixture",
            "app_config": {
                "app": {
                    "image": "deluan/navidrome:latest",
                    "data_path": "/opt/navidrome-fixture/data",
                    "port": 14533,
                    "container_port": 4533,
                    "puid": 1313,
                    "pgid": 1313,
                }
            },
            "homelabinfra_config": {"timezone": "UTC"},
            "_navidrome_music_path": "/srv/fixtures/media/music",
        },
    )
    assert 'user: "1313:1313"' in compose, "'user: \"1313:1313\"' in compose"
    assert "ND_DATAFOLDER=/data" in compose, "'ND_DATAFOLDER=/data' in compose"
    assert "ND_MUSICFOLDER=/music" in compose, "'ND_MUSICFOLDER=/music' in compose"
    assert (
        '"/opt/navidrome-fixture/data:/data"' in compose
    ), "'\"/opt/navidrome-fixture/data:/data\"' in compose"
    assert (
        '"/srv/fixtures/media/music:/music:ro"' in compose
    ), "'\"/srv/fixtures/media/music:/music:ro\"' in compose"
    assert (
        '"/srv/fixtures/media:/music' not in compose
    ), "'\"/srv/fixtures/media:/music' not in compose"
    assert ":rw" not in compose, "':rw' not in compose"
    assert '"14533:4533"' in compose, "'\"14533:4533\"' in compose"
    role_tasks = yaml.safe_load(read("ansible/roles/navidrome/tasks/main.yml"))
    ownership_guard = task_named(
        role_tasks, "Assert Navidrome storage owner matches its container user"
    )["ansible.builtin.assert"]["that"]
    ownership_context = {
        "app_config": {
            "media_storage": {"owner": {"uid": 1313, "gid": 1313}},
        },
        # Ansible's set_fact currently exposes these values as unsafe text even when
        # the source expression used the int filter.
        "_navidrome_uid": "1313",
        "_navidrome_gid": "1313",
    }
    for expression in ownership_guard:
        assert (
            render("{{ " + expression + " }}", ownership_context) is True
        ), "matching media ownership survives Ansible's stringified set_fact ids"
    mount_matcher = task_named(
        role_tasks, "Match Navidrome music path against declared media mounts"
    )
    mount_match_condition = mount_matcher["when"]
    assert "{{" not in mount_match_condition and "{%" not in mount_match_condition
    for music_path, mount_path, expected in (
        ("/srv/fixtures/media/music", "/srv/fixtures/media", True),
        ("/srv/fixtures/media", "/srv/fixtures/media", True),
        ("/srv/fixtures/media-backup/music", "/srv/fixtures/media", False),
        ("/srv/fixtures/other/music", "/srv/fixtures/media", False),
    ):
        assert (
            render(
                "{{ " + mount_match_condition + " }}",
                {
                    "_navidrome_music_path": music_path,
                    "item": {"path": mount_path},
                },
            )
            is expected
        ), f"Navidrome mount match for {music_path} under {mount_path}"
    mount_guard = task_named(
        role_tasks, "Assert Navidrome music path is inside a declared media mount"
    )["ansible.builtin.assert"]["that"]
    assert (
        render("{{ " + mount_guard + " }}", {"_navidrome_music_path_mounted": True})
        is True
    ), "mounted path is accepted"
    assert (
        render("{{ " + mount_guard + " }}", {"_navidrome_music_path_mounted": False})
        is False
    ), "unmounted path is rejected"


def maintainerr_rendering():
    context = {
        "instance": "maintainerr",
        "app_config": {
            "app": {
                "storage": 1,
                "port": 6246,
                "image": "ghcr.io/maintainerr/maintainerr:latest",
            },
            "backup": {
                "client": {
                    "image": "buildpack-deps:bookworm-curl",
                    "key_url": "key",
                    "repository": "repo",
                    "suite": "bookworm",
                    "component": "main",
                }
            },
        },
        "homelabinfra_config": {"timezone": "UTC"},
        "maintainerr_fqdn": "maintainerr.example.test",
        "client": {
            "image": "buildpack-deps:bookworm-curl",
            "key_url": "key",
            "repository": "repo",
            "suite": "bookworm",
            "component": "main",
        },
        "_js_r_job_name": "maintainerr-restore-plan",
        "_js_r_target": "maintainerr",
        "_js_r_mode": "plan",
        "_js_r_timeout": 900,
        "_js_r_secret": "maintainerr-restore",
        "_js_r_backup_id": "maintainerr",
        "_js_r_snapshot": "",
        "k8s_pbs_repository": "pbs@host:store",
        "k8s_pbs_fingerprint": "fingerprint",
    }

    def documents(path):
        return list(yaml.safe_load_all(render(path.read_text(), context)))

    workload = documents(ROOT / "ansible/roles/maintainerr/templates/manifest.yaml.j2")
    restore = documents(
        ROOT / "ansible/roles/maintainerr/templates/restore-job.yaml.j2"
    )
    workload_pvc = next(
        (
            item["metadata"]["name"]
            for item in workload
            if item and item["kind"] == "PersistentVolumeClaim"
        )
    )
    restore_pvc = next(
        (
            volume["persistentVolumeClaim"]["claimName"]
            for volume in restore[0]["spec"]["template"]["spec"]["volumes"]
            if "persistentVolumeClaim" in volume
        )
    )
    assert (
        restore_pvc == workload_pvc
    ), f"restore PVC {restore_pvc!r} != workload PVC {workload_pvc!r}"


def mautic_rendering():
    tasks = yaml.safe_load(read("ansible/roles/mautic/tasks/main.yml"))
    converge = task_named(
        tasks, "Mautic | Converge platform-owned configuration fields"
    )
    fields_expr = converge["vars"]["_mautic_local_php_fields"]
    line_expr = converge["ansible.builtin.lineinfile"]["line"]
    app_config = {
        "app": {
            "database_host": "h",
            "database_port": 3306,
            "database": {"name": "mautic"},
            "database_user": "u",
            "database_password": "p",
            "name": "Mautic",
        }
    }
    for installed in (False, True):
        for enabled in (False, True):
            for encryption, scheme in (
                ("starttls", "smtp"),
                ("tls", "smtps"),
                ("none", "smtp"),
            ):
                mail = {
                    "enabled": enabled,
                    "from_name": "Fixture",
                    "from_address": "a@example.test",
                    "host": "smtp.example.test",
                    "port": 587,
                    "username": "user@example.test",
                    "password": "p@ss:w/ord'\\%x",
                    "encryption": encryption,
                }
                variables = {
                    "app_config": app_config,
                    "wiring_mail": mail,
                    "mautic_fqdn": "mautic.example.test",
                    "ansible_managed": "fixture",
                    "_mautic_installed_marker": {"stat": {"exists": installed}},
                }
                fields = render(fields_expr, variables)
                by_key = {f["key"]: f["value"] for f in fields}
                assert (
                    "db_driver" in by_key
                ) == installed, "database sentinel must follow installation"
                assert (
                    "site_url" in by_key
                ) == installed, "site URL must follow installation"
                assert "mailer_transport" not in by_key, "Mautic reads mailer_dsn"
                seed = render(
                    read("ansible/roles/mautic/templates/local.php.j2"), variables
                )
                assert (
                    "'db_driver' =>" not in seed and "'site_url' =>" not in seed
                ), "seed must omit installed sentinels"
                if not enabled:
                    assert (
                        by_key["mailer_dsn"] == "smtp://localhost:25"
                    ), "disabled mail must reset DSN"
                    continue
                field = next(f for f in fields if f["key"] == "mailer_dsn")
                dsn = field["value"]
                parsed = urllib.parse.urlsplit(dsn)
                assert parsed.scheme == scheme, f"wrong scheme for {encryption}"
                assert (
                    urllib.parse.unquote(parsed.username) == mail["username"]
                ), "username must round trip"
                assert (
                    urllib.parse.unquote(parsed.password) == mail["password"]
                ), "password must round trip"
                assert (parsed.hostname, parsed.port) == (
                    mail["host"],
                    mail["port"],
                ), "relay must round trip"
                line = render(line_expr, {"item": field})
                value = line.split("=> '", 1)[1].rsplit("',", 1)[0]
                assert value.count("%%") >= dsn.count(
                    "%"
                ), "Symfony DI percents must be doubled"
                assert value.replace("%%", "%") == dsn, "on-disk DSN must round trip"
                assert (
                    line.strip() in seed
                ), "seed and convergence must encode the same DSN"
    normalize = task_named(
        tasks, "Mautic | Normalize a blank mail.encryption to its documented default"
    )
    normalized = render(
        normalize["ansible.builtin.set_fact"]["wiring_mail"],
        {"wiring_mail": {"encryption": "", "host": "h", "port": 25}},
    )
    assert normalized == {
        "encryption": "starttls",
        "host": "h",
        "port": 25,
    }, "blank encryption must normalize"
    refuse = task_named(
        tasks, "Mautic | Refuse a mail.encryption this app cannot actually enforce"
    )
    for encryption, allowed in (("starttls", False), ("tls", True), ("none", True)):
        result = render(
            "{{ " + refuse["ansible.builtin.assert"]["that"] + " }}",
            {"wiring_mail": {"enabled": True, "encryption": encryption}},
        )
        assert result == allowed, f"mail encryption guard for {encryption}"


def mail_consumers_open_smtp_egress():
    tasks = yaml.safe_load(read("ansible/tasks/firewall/open-app-access.yml"))
    access = task_named(tasks, "Firewall access | Add SMTP egress for a mail consumer")
    expression = access["ansible.builtin.set_fact"]["_fw_app_access"]
    declared = {"egress": [{"name": "nntp", "protocol": "TCP", "port": 119}]}
    on = render(expression, {"wiring_firewall": {}, "firewall_sends_mail": True,
                             "wiring_mail": {"enabled": True, "port": "587"}})
    assert on["egress"] == [{"name": "smtp", "protocol": "TCP", "port": "587"}], on
    off = render(expression, {"wiring_firewall": {}, "firewall_sends_mail": True,
                              "wiring_mail": {"enabled": False, "port": ""}})
    assert off["egress"] == [], off
    both = render(expression, {"wiring_firewall": declared, "firewall_sends_mail": True,
                               "wiring_mail": {"enabled": True, "port": "465"}})
    assert both["egress"] == declared["egress"] + [{"name": "smtp", "protocol": "TCP", "port": "465"}], both
    for app in ("mautic", "odoo", "bookstack"):
        names = [play["name"] for play in yaml.safe_load(read(f"ansible/playbooks/apps/{app}.yml"))]
        opened = [i for i, n in enumerate(names) if n.endswith("| Open network access")]
        deployed = [i for i, n in enumerate(names) if n.endswith("| Deploy")]
        assert opened and opened[0] < deployed[0], (app, names)


def wireguard_opens_its_udp_forward_before_deploy():
    plays = yaml.safe_load(read("ansible/playbooks/apps/wireguard.yml"))
    names = [play["name"] for play in plays]
    assert names.index("WireGuard | Open network access") < names.index("WireGuard | Deploy"), names
    fw_play = plays[names.index("WireGuard | Open network access")]
    defaults = yaml.safe_load(read("ansible/vars/app-defaults/wireguard.yml"))["wireguard_defaults"]
    host = {"ansible_host": "192.0.2.20", "app_config": defaults}
    variables = {"instance": "wireguard", "groups": {"deploy_wireguard": ["wg"]}, "hostvars": {"wg": host}}
    firewall = render(fw_play["vars"]["wiring_firewall"], {**variables, "_wg_fw_host": host})
    assert firewall.get("egress", []) == [], firewall
    assert firewall["inbound"] == [{"name": "wireguard", "protocol": "UDP",
                                    "port": defaults["app"]["port"], "enabled": True}], firewall
    task = task_named(fw_play["tasks"], "Open the app's network access")
    assert task["ansible.builtin.include_tasks"] == "../../tasks/firewall/open-app-access.yml", task
    ports = render(task["vars"]["firewall_inbound_ports"], {**variables, "_wg_fw_host": host})
    assert ports == {"wireguard": str(defaults["app"]["port"])}, ports


def wireguard_remove_preserves_vaultwarden_configuration():
    remove = yaml.safe_load(read("ansible/playbooks/maintenance/remove-wireguard.yml"))
    tasks = [task for play in remove for task in play.get("tasks", [])]
    commands = [task.get("ansible.builtin.command", {}) for task in tasks]
    assert not any(command.get("argv", [])[:2] == ["bw", "delete"] for command in commands), commands
    assert any(command.get("argv", [])[:2] == ["pct", "destroy"] for command in commands), commands
    assert any("tasks/unwiring/firewall-" in task.get("ansible.builtin.include_tasks", "")
               for task in tasks), tasks

    catalog = yaml.safe_load(read("catalog/applications.yml"))["applications"]["wireguard"]
    actions = set(catalog.get("actions", []))
    assert not actions.intersection({"backup", "restore"}), actions


def wireguard_peer_address_uses_the_ipaddr_filter():
    tasks = yaml.safe_load(read("ansible/roles/wireguard/tasks/peer.yml"))
    validation = next(task["ansible.builtin.assert"] for task in tasks
                      if task.get("name") == "WireGuard | Validate peer declaration")
    checks = validation["that"]
    assert any(check == "(_wg_peer.vpn_address | ansible.utils.ipaddr) is string"
               for check in checks), checks
    assert not any("is ansible.utils.ipaddr" in check for check in checks), checks


def apps_open_access_through_the_shared_task():
    # Apps use one task file for firewall access; it reaches the provider only when one is
    # configured and refuses a forward that misses the app's port. qBittorrent applies its
    # source-port policy after Deploy has enforced the matching client settings.
    tasks = yaml.safe_load(read("ansible/tasks/firewall/open-app-access.yml"))
    provider = task_named(tasks, "Firewall access | Open it on the configured provider")
    for name, expected in (("opnsense", True), ("none", False)):
        infra = {"firewall": {"provider": name}}
        results = [render("{{ " + condition + " }}", {"homelabinfra_infra": infra})
                   for condition in provider["when"]]
        assert all(results) is expected, (name, results)

    check = task_named(tasks, "Firewall access | Assert each enabled forward lands on the app's port")
    inbound = [{"name": "listen", "port": 6881, "enabled": True},
               {"name": "off", "port": 1, "enabled": False},
               {"name": "other", "port": 2, "enabled": True}]
    checked = render(check["loop"], {"wiring_firewall": {"inbound": inbound},
                                     "firewall_inbound_ports": {"listen": "6881", "off": "9"}})
    assert [f["name"] for f in checked] == ["listen"], checked
    for forward, port, ok in (({"name": "listen", "port": 6881}, "6881", True),
                              ({"name": "listen", "port": 6881}, "6882", False),
                              ({"name": "listen", "port": 443, "local_port": 6881}, "6881", True)):
        result = render("{{ " + check["ansible.builtin.assert"]["that"] + " }}",
                        {"_fw_forward": forward, "firewall_inbound_ports": {"listen": port}})
        assert result is ok, (forward, port, result)

    ports = {"plex": ("_plex_fw_host", "plex-remote-access", "port"),
             "qbittorrent": ("_qbt_fw_host", "bittorrent-listen", "torrenting_port"),
             "wireguard": ("_wg_fw_host", "wireguard", "port")}
    for app in ("slskd", "qbittorrent", "sabnzbd", "plex", "wireguard"):
        plays = yaml.safe_load(read(f"ansible/playbooks/apps/{app}.yml"))
        names = [play["name"] for play in plays]
        opened = next(i for i, n in enumerate(names) if n.endswith("| Open network access"))
        deployed = next(i for i, n in enumerate(names) if n.endswith("| Deploy"))
        if app == "qbittorrent":
            assert deployed < opened, (app, names)
        else:
            assert opened < deployed, (app, names)
        task = task_named(plays[opened]["tasks"], "Open the app's network access")
        assert task["ansible.builtin.include_tasks"] == "../../tasks/firewall/open-app-access.yml", app
        if app in ports:
            host, forward, key = ports[app]
            defaults = yaml.safe_load(read(f"ansible/vars/app-defaults/{app}.yml"))[f"{app}_defaults"]
            declared = {f["name"]: f["port"] for f in defaults["firewall"]["inbound"]}
            rendered = render(task["vars"]["firewall_inbound_ports"],
                              {host: {"app_config": defaults}, "instance": app,
                               "homelabinfra_config": {}})
            assert rendered == {forward: str(defaults["app"][key])} and declared[forward] == defaults["app"][key], (
                app, rendered, declared)

def qbittorrent_enforces_peer_ports_before_start():
    tasks = yaml.safe_load(read("ansible/roles/qbittorrent/tasks/main.yml"))
    names = [task["name"] for task in tasks if "name" in task]
    check = names.index("Check qBittorrent's outgoing peer port settings")
    enforce = names.index("Enforce qBittorrent's outgoing peer port settings when they drift")
    start = names.index("Start the app")
    flush = names.index("Apply pending restarts before the WebUI checks")
    assert check < enforce < start < flush, names
    assert not any("Ensure qBittorrent is started after updating its port settings" in name
                   for name in names), names


def k3s_defaults_carry_no_lab_topology():
    # A Deploy with no config/apps/k3s-cluster.yml must stop before Proxmox, naming what is missing.
    defaults = yaml.safe_load(read("ansible/vars/app-defaults/k3s-cluster.yml"))["k3s_cluster_defaults"]
    example = yaml.safe_load(read("config.example/apps/k3s-cluster.example.yml"))
    play = yaml.safe_load(read("ansible/playbooks/apps/k3s-cluster.yml"))[0]
    fallback = task_named(play["pre_tasks"], "Use the lab-wide storage unless the cluster names its own")
    check = task_named(play["tasks"], "Assert the cluster's topology is declared")

    def resolve(instance, lab_storage):
        merged = render("{{ d | combine(i, recursive=True) }}", {"d": defaults, "i": instance})
        merged = render(fallback["ansible.builtin.set_fact"]["k8s_cluster_config"],
                        {"k8s_cluster_config": merged,
                         "homelabinfra_config": {"proxmox": {"storage": lab_storage}}})
        missing = render(check["vars"]["_k3s_missing"], {"k8s_cluster_config": merged})
        return merged, missing

    _, missing = resolve({}, "")
    assert [m.split(" ")[0] for m in missing] == [
        "cluster.nodes", "ingress.vip", "ingress.address_pool", "proxmox.storage"], missing
    merged, missing = resolve(example, "lab-pool")
    assert missing == [], missing
    assert merged["proxmox"]["storage"] == "lab-pool", merged["proxmox"]


def k3s_template_supports_a_per_node_snapshotter():
    template = read("ansible/roles/k3s_cluster/templates/config.yaml.j2")
    node_spec = {"ip_address": "192.0.2.23", "taints": []}
    base = {
        "_k3s_is_founder": True,
        "inventory_hostname": "k3s-3",
        "k8s_cluster_token": "",
        "k8s_node_spec": node_spec,
        "k8s_cluster_config": {
            "cluster": {"nodes": [{"ip_address": "192.0.2.23"}]}
        },
    }
    default_config = yaml.safe_load(render(template, base))
    overlayfs_config = yaml.safe_load(
        render(template, {
            **base,
            "k8s_node_spec": {**node_spec, "snapshotter": "overlayfs"},
        })
    )
    native_config = yaml.safe_load(
        render(template, {
            **base,
            "k8s_node_spec": {**node_spec, "snapshotter": "native"},
        })
    )

    tasks = yaml.safe_load(read("ansible/roles/k3s_cluster/tasks/main.yml"))
    validation = task_named(tasks, "k3s | Validate the node snapshotter")

    assert "snapshotter" not in default_config, default_config
    assert overlayfs_config["snapshotter"] == "overlayfs", overlayfs_config
    assert native_config["snapshotter"] == "native", native_config
    snapshotter_assertion = validation["ansible.builtin.assert"]["that"][0]
    assert snapshotter_assertion == (
        "(k8s_node_spec.snapshotter | default('overlayfs')) in ['overlayfs', 'native']"
    ), validation
    cases = ((None, True), ("overlayfs", True), ("native", True), ("zfs", False))
    for snapshotter, expected in cases:
        node = dict(node_spec)
        if snapshotter is not None:
            node["snapshotter"] = snapshotter
        result = render(
            "{{ " + snapshotter_assertion + " }}",
            {"k8s_node_spec": node},
        )
        assert result is expected, (snapshotter, result)


def vm_clone_keeps_recorded_app_tags_and_notes():
    # A redeploy must not strip what apps recorded on the guest (record-app-on-guest.yml).
    tasks = yaml.safe_load(read("ansible/tasks/proxmox/vm-clone.yml"))
    merge = task_named(tasks, "VM clone | Merge the recorded app tags and notes into the request")
    region = "<!-- homelab-infra:apps -->\n| app | kind |\n<!-- /homelab-infra:apps -->"
    current = {"tags": "_+lab;_-k3s;_.shared;_jellyseerr;_old-platform-lane;operator-note;_.stale",
               "description": "k3s cluster node, created by homelab-infra\n\n" + region}
    variables = dict(merge["vars"])
    variables.update({
        "_vmc_existing_config": {"stdout": json.dumps(current)},
        "homelabinfra_config": {"proxmox": {"vm": {
            "tags": ["_+lab", "_-k3s", "_.shared"],
            "description": "k3s cluster node, created by homelab-infra"}}},
    })
    tags = render(merge["ansible.builtin.set_fact"]["_vmc_tags"], variables)
    notes = render(merge["ansible.builtin.set_fact"]["_vmc_description"], variables)
    assert tags == sorted(["_+lab", "_-k3s", "_.shared", "_jellyseerr", "_old-platform-lane", "operator-note"]), tags
    assert notes == current["description"], notes
    variables["_vmc_existing_config"] = {}
    assert render(merge["ansible.builtin.set_fact"]["_vmc_tags"], variables) == ["_+lab", "_-k3s", "_.shared"]

def bazarr_wiring_sends_lowercase_booleans():
    # Bazarr converts only lowercase 'true'/'false'; 'True' fails its type check with 406.
    block = yaml.safe_load(read("ansible/tasks/app-wiring/bazarr-arr.yml"))[0]["block"]
    task = task_named(block, "Bazarr | Apply the connection")
    for ssl, expected in (("False", "false"), ("True", "true")):
        body = render(task["ansible.builtin.uri"]["body"], {
            "_mw_section": "radarr",
            "_mw_wanted": {"ip": "192.0.2.10", "port": "7878", "apikey": "k",
                           "ssl": ssl, "base_url": ""},
        })
        assert body["settings-general-use_radarr"] == "true", body
        assert body["settings-radarr-ssl"] == expected, body
        assert body["settings-radarr-port"] == "7878", body
        
def flaresolverr_rendering():
    defaults = yaml.safe_load(
        read("ansible/vars/app-defaults/flaresolverr.yml")
    )["flaresolverr_defaults"]
    assert defaults["app"]["log_level"] == "info", "default log level"

    app_config = {
        **defaults,
        "app": {**defaults["app"], "log_level": "debug"},
    }
    manifest = render(
        read("ansible/roles/flaresolverr/templates/manifest.yaml.j2"),
        {
            "instance": "flaresolverr",
            "app_config": app_config,
            "homelabinfra_config": {"timezone": "UTC"},
        },
    )
    deployment = next(
        doc
        for doc in yaml.safe_load_all(manifest)
        if doc and doc["kind"] == "Deployment"
    )
    environment = {
        item["name"]: item["value"]
        for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert environment["LOG_LEVEL"] == "debug", "configured log level reaches pod"


def immich_machine_learning_uses_only_a_declared_gpu_device():
    template = read("ansible/roles/immich/templates/docker-compose.yml.j2")

    def machine_learning_service(gpu_device, render_node, image):
        rendered = render(
            template,
            {
                "ansible_managed": "Managed by gate test",
                "instance": "immich",
                "immich_env_file": "/opt/immich/immich.env",
                "_immich_media_path": "/mnt/media/p2-immich",
                "app_config": {
                    "app": {
                        "image": "ghcr.io/immich-app/immich-server:v3.1.0",
                        "machine_learning_image": image,
                        "model_cache_path": "/opt/immich/model-cache",
                        "port": 2283,
                        "gpu_device": gpu_device,
                        "gpu_render_node": render_node,
                    },
                    "media_storage": {"owner": {"uid": 1313, "gid": 1313}},
                },
            },
        )
        services = yaml.safe_load(rendered)["services"]
        assert services["immich-server"]["user"] == "1313:1313"
        assert "user" not in services["immich-machine-learning"]
        return services["immich-machine-learning"]

    cpu = machine_learning_service(
        "", "/dev/dri/renderD128", "ghcr.io/immich-app/immich-machine-learning:v3.1.0"
    )
    assert "devices" not in cpu, cpu
    assert cpu["image"] == "ghcr.io/immich-app/immich-machine-learning:v3.1.0", cpu

    hardware = machine_learning_service(
        "intel-igpu",
        "/dev/dri/renderD128",
        "ghcr.io/immich-app/immich-machine-learning:v3.1.0-openvino",
    )
    assert hardware["devices"] == ["/dev/dri/renderD128:/dev/dri/renderD128"], hardware


def comfyui_accelerator_profile_controls_gpu_runtime():
    template = read("ansible/roles/comfyui/templates/docker-compose.yml.j2")

    def service(accelerator):
        rendered = render(
            template,
            {
                "ansible_managed": "Managed by gate test",
                "instance": "comfyui",
                "app_config": {
                    "app": {
                        "accelerator": accelerator,
                        "image": (
                            "ghcr.io/ai-dock/comfyui@sha256:5f08ab173b4d78babb6b003a875d495a64378815237e8b80c9248923dcda9a52"
                            if accelerator == "cpu"
                            else "ghcr.io/ai-dock/comfyui:latest-cuda"
                        ),
                        "port": 8188,
                        "models_path": "/opt/comfyui/storage/models",
                        "config_path": "/opt/comfyui/storage/user",
                        "custom_nodes_path": "/opt/comfyui/storage/custom_nodes",
                        "input_path": "/opt/comfyui/storage/input",
                        "output_path": "/opt/comfyui/storage/output",
                    }
                },
            },
        )
        return yaml.safe_load(rendered)["services"]["comfyui"]

    role_tasks = yaml.safe_load(read("ansible/roles/comfyui/tasks/main.yml"))
    cpu = service("cpu")
    assert cpu["image"] == (
        "ghcr.io/ai-dock/comfyui@sha256:5f08ab173b4d78babb6b003a875d495a64378815237e8b80c9248923dcda9a52"
    ), cpu
    assert "gpus" not in cpu, cpu
    assert "deploy" not in cpu, cpu
    assert "/opt/comfyui/storage/user:/opt/ComfyUI/user" in cpu["volumes"], cpu
    healthcheck = cpu["healthcheck"]
    assert healthcheck["test"][0] == "CMD-SHELL", healthcheck
    assert "--max-redirect=0" in healthcheck["test"][1], healthcheck
    assert "HTTP/[^ ]+ (200|302)( |$)" in healthcheck["test"][1], healthcheck
    migration = yaml.safe_load(read("ansible/roles/comfyui/tasks/migrate-user-data.yml"))
    migration_block = next(
        task for task in migration if task.get("name") == "ComfyUI user data | Migrate existing container files"
    )
    assert any(
        task.get("name")
        == "ComfyUI user data | Merge files without replacing persistent data"
        for task in migration_block["block"]
    ), migration
    migration_index = next(
        i for i, task in enumerate(role_tasks)
        if task.get("name") == "ComfyUI | Preserve existing user data before mounting persistent storage"
    )
    render_index = next(
        i for i, task in enumerate(role_tasks)
        if task.get("name") == "ComfyUI | Render the Compose project"
    )
    assert migration_index < render_index, role_tasks

    cuda = service("cuda")
    assert cuda["image"].endswith(":latest-cuda"), cuda
    assert cuda["gpus"] == "all", cuda
    assert cuda["deploy"]["resources"]["reservations"]["devices"] == [
        {"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}
    ], cuda

    gpu_checks = [task for task in role_tasks if "NVIDIA" in task["name"] or "GPU" in task["name"]]
    assert len(gpu_checks) == 4, gpu_checks
    assert all(
        task["when"] == "app_config.app.accelerator | default('cuda') == 'cuda'"
        for task in gpu_checks
    ), gpu_checks

    role_defaults = yaml.safe_load(read("ansible/roles/comfyui/defaults/main.yml"))
    health = next(
        task for task in role_tasks if task["name"] == "ComfyUI | Wait for the system stats endpoint"
    )
    assert health["ansible.builtin.uri"]["follow_redirects"] == "none"
    assert health["ansible.builtin.uri"]["status_code"] == [200, 302]
    assert health["until"] == "_comfyui_health.status | default(0) in [200, 302]"
    assert role_defaults["comfyui_cpu_http_retries"] == 120
    assert role_defaults["comfyui_http_retries"] == 60
    retries = health["retries"]
    assert render(
        retries,
        {
            "comfyui_cpu_http_retries": 120,
            "comfyui_http_retries": 60,
            "app_config": {"app": {"accelerator": "cpu"}},
        },
    ) == "120"
    assert render(
        retries,
        {
            "comfyui_cpu_http_retries": 120,
            "comfyui_http_retries": 60,
            "app_config": {"app": {"accelerator": "cuda"}},
        },
    ) == "60"

    playbook = yaml.safe_load(read("ansible/playbooks/apps/comfyui.yml"))

    def find_task(tasks, name):
        for task in tasks:
            if task.get("name") == name:
                return task
            for key in ("block", "rescue", "always"):
                found = find_task(task.get(key, []), name)
                if found:
                    return found
        return None

    provision = playbook[0]["tasks"]
    cloud_template = find_task(provision, "Ensure the Debian cloud-init template exists")
    clone = find_task(provision, "Clone the ComfyUI VM from the cloud-init template")
    attach = find_task(provision, "Attach the named ComfyUI PCI device")
    assert cloud_template and clone and attach
    placement = cloud_template["vars"]["proxmox_placement_class"]
    assert render(placement, {"app_config": {"app": {"accelerator": "cpu"}}}) == "floating_vm"
    assert render(placement, {"app_config": {"app": {"accelerator": "cuda"}}}) == "gpu_vm"
    assert clone["vars"]["proxmox_placement_class"] == placement
    assert attach["when"] == "app_config.app.accelerator | default('cuda') == 'cuda'"


def immich_task_defaults_missing_gpu_device_to_empty():
    tasks = yaml.safe_load(read("ansible/roles/immich/tasks/main.yml"))
    task = task_named(tasks, "Immich | Store the backend and device contract in Vaultwarden")
    expression = task["vars"]["vault_item_fields"]["gpu_device"]

    assert render(expression, {"app_config": {"app": {}}}) == ""
    assert (
        render(
            expression,
            {"app_config": {"app": {"gpu_device": "intel-igpu"}}},
        )
        == "intel-igpu"
    )


def backup_prune_keeps_the_previous_point():
    # A same-day pre-restore backup must not prune the recovery point being restored, in
    # any app: Kubernetes CronJobs, native backup tasks and the recovery scripts.
    templates = sorted(ROOT.glob("ansible/roles/*/templates/backup-cronjob.yaml.j2"))
    assert templates, "no Kubernetes backup templates found"
    for template in templates:
        assert "proxmox-backup-client prune" in template.read_text(), f"{template} has no prune"
    scripts = [path for path in sorted(ROOT.glob("ansible/roles/*/*/*"))
               if path.is_file() and "--keep-daily" in path.read_text(errors="ignore")]
    assert len(scripts) > len(templates), scripts
    for script in scripts:
        contents = script.read_text()
        command_argvs = []
        if script.suffix in {".yml", ".yaml"}:
            document = yaml.safe_load(contents)

            def collect_argvs(value):
                if isinstance(value, dict):
                    for key in ("ansible.builtin.command", "command"):
                        command = value.get(key)
                        if isinstance(command, dict) and isinstance(command.get("argv"), list):
                            command_argvs.append(command["argv"])
                    for child in value.values():
                        collect_argvs(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_argvs(child)

            collect_argvs(document)

        has_prune_argv = any("prune" in [str(token) for token in argv] for argv in command_argvs)
        for line in contents.splitlines():
            if "--keep-daily" in line and not line.lstrip().startswith("#"):
                if has_prune_argv and script.suffix in {".yml", ".yaml"}:
                    continue
                assert "--keep-last 2 --keep-daily" in line, f"{script} prunes without --keep-last 2"

        for argv in command_argvs:
            tokens = [str(token) for token in argv]
            if "prune" not in tokens:
                continue
            prune_options = tokens[tokens.index("prune") + 1:]
            keep_last = prune_options.index("--keep-last") if "--keep-last" in prune_options else -1
            keep_daily = prune_options.index("--keep-daily") if "--keep-daily" in prune_options else -1
            assert keep_last >= 0 and keep_daily > keep_last + 1 and prune_options[keep_last + 1] == "2", (
                f"{script} prunes without --keep-last 2 before --keep-daily"
            )


def k3s_pbs_backup_jobs_read_the_rotated_fingerprint():
    # These CronJobs live for months between app deploys. Keep the fingerprint in a
    # namespace ConfigMap and have PBS refresh it after reading the active certificate.
    fingerprint = ":".join(["AA"] * 32)
    backup = {
        "schedule": "0 3 * * *",
        "retention_days": 14,
        "timeout_seconds": 1800,
        "scratch_size": 2,
        "client": {
            "image": "buildpack-deps:bookworm-curl",
            "key_url": "https://example.test/key",
            "repository": "https://example.test/repo",
            "suite": "bookworm",
            "component": "main",
        },
    }
    for app in ("jellyseerr", "litellm", "maintainerr", "mixpost"):
        path = f"ansible/roles/{app}/templates/backup-cronjob.yaml.j2"
        template = read(path)
        assert "kind: ConfigMap" in template, path
        assert "name: pbs-backup-trust" in template, path
        assert "fingerprint: \"{{ k8s_pbs_fingerprint }}\"" in template, path
        assert "homelab-infra.io/pbs-fingerprint: managed" in template, path
        fingerprint_env = template.split("- name: PBS_FINGERPRINT", 1)[1].split(
            "- name:", 1
        )[0]
        assert "configMapKeyRef:" in fingerprint_env, path
        assert "name: pbs-backup-trust" in fingerprint_env, path
        assert "key: fingerprint" in fingerprint_env, path
        assert "value: \"{{" not in fingerprint_env, path

        context = {
            "instance": app,
            "k8s_namespace": f"app-{app}",
            "k8s_pbs_fingerprint": fingerprint,
            "app_config": {
                "backup": backup,
                "app": {
                    "database": {"version": 16, "image": "mysql:8", "name": app},
                },
            },
            "_js_backup_id": app,
            "_js_backup_datastore": "store",
            "_js_backup_repository": "token@pbs:store",
            "_js_backup_timezone": "UTC",
            "_js_ntfy_url": "",
            "_js_ntfy_token": "",
            "_litellm_backup_id": app,
            "_litellm_backup_repository": "token@pbs:store",
            "_litellm_backup_timezone": "UTC",
            "_litellm_database_host": "postgres",
            "_litellm_database_name": app,
            "_litellm_database_port": 5432,
            "_litellm_database_user": app,
            "_litellm_ntfy_url": "",
            "_litellm_ntfy_token": "",
            "_mp_backup_id": app,
            "_mp_backup_datastore": "store",
            "_mp_backup_repository": "token@pbs:store",
            "_mp_backup_timezone": "UTC",
            "_mp_ntfy_url": "",
            "_mp_ntfy_token": "",
        }
        documents = list(yaml.safe_load_all(render(template, context)))
        trust = next(item for item in documents if item["kind"] == "ConfigMap")
        cronjob = next(item for item in documents if item["kind"] == "CronJob")
        assert trust["metadata"]["name"] == "pbs-backup-trust", path
        assert trust["data"]["fingerprint"] == fingerprint, path
        environment = (
            cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]
        )
        fingerprint_setting = next(
            item for item in environment if item["name"] == "PBS_FINGERPRINT"
        )
        assert fingerprint_setting["valueFrom"]["configMapKeyRef"] == {
            "name": "pbs-backup-trust",
            "key": "fingerprint",
        }, path


def litellm_recovery_jobs_use_the_database_client_major():
    templates = (
        "ansible/roles/litellm/templates/backup-cronjob.yaml.j2",
        "ansible/roles/litellm/templates/restore-job.yaml.j2",
    )
    for path in templates:
        template = read(path)
        assert "https://www.postgresql.org/media/keys/ACCC4CF8.asc" in template, path
        assert "${VERSION_CODENAME}-pgdg main" in template, path
        assert '"postgresql-client-${PG_CLIENT_VERSION}"' in template, path
        assert "name: PG_CLIENT_VERSION" in template, path
    backup = read(templates[0])
    restore = read(templates[1])
    assert 'value: "{{ app_config.app.database.version | int }}"' in backup
    assert 'value: "{{ restore_target_config.app.database.version | int }}"' in restore
    assert "install -d -m 0755 /etc/apt/keyrings /restore/database /restore/config" not in restore
    for option in ("--ignore-acls true", "--ignore-xattrs true", "--ignore-ownership true", "--ignore-permissions true"):
        assert restore.count(option) == 2, f"LiteLLM restore must ignore scratch metadata: {option}"
    assert "mountPath: /root/.cache" in restore
    assert "medium: Memory" in restore
    assert 'name: TMPDIR\n              value: /restore/tmp' in restore
    restore_tasks = yaml.safe_load(read("ansible/roles/litellm/tasks/restore.yml"))
    contract = task_named(restore_tasks, "Restore | Require matching PostgreSQL state contracts")
    assert any(
        "app_config.app.database.version" in expression
        and "restore_target_config.app.database.version" in expression
        for expression in contract["ansible.builtin.assert"]["that"]
    )
    replace = task_named(restore_tasks, "Restore | Replace LiteLLM state from PBS")
    extract_config = task_named(
        replace["block"], "Restore | Extract the archived routing ConfigMap"
    )
    extract_expression = extract_config["ansible.builtin.set_fact"][
        "_litellm_restored_config_b64"
    ]
    assert render(
        extract_expression, {"k8s_job_log": "ROUTING_CONFIG_B64=YWJj\n"}
    ) == "YWJj"
    assert render(
        extract_expression, {"k8s_job_log": "database restore complete\n"}
    ) == ""


def plane_environment_context(plane_fqdn):
    return {
        "ansible_managed": "Managed by gate test",
        "plane_fqdn": plane_fqdn,
        "app_config": {
            "app": {
                "release": "v1.4.2",
                "object_storage": {"bucket": "plane"},
                "database": {"name": "plane"},
                "database_user": "fixture-db-user",
                "database_password": "fixture-db-password",
                "database_host": "postgresql-plane",
                "database_port": 5432,
                "rabbitmq": {
                    "user": "plane",
                    "host": "plane-mq",
                    "port": 5672,
                    "vhost": "plane",
                },
            }
        },
        "_plane_redis_backend": {"host": "redis-plane", "port": 6379},
        "_plane_redis_password": "fixture-redis-password",
        "_plane_rabbitmq_password": "fixture-rabbitmq-password",
        "_plane_object_storage_access_key": "fixture-access-key",
        "_plane_object_storage_secret_key": "fixture-object-storage-secret",
        "_plane_secret_key": "fixture-plane-secret",
        "_plane_live_server_secret_key": "fixture-live-secret",
    }


def plane_env_matches_the_bundled_proxy_and_api_contract():
    template = read("ansible/roles/plane/templates/plane.env.j2")
    output = render(template, plane_environment_context("plane.example.test"))
    environment = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in output.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }

    assert environment["APP_DOMAIN"] == "plane.example.test"
    assert environment["BUCKET_NAME"] == "plane"
    assert environment["AWS_S3_BUCKET_NAME"] == "plane"
    assert environment["GUNICORN_WORKERS"] == "1"
    assert environment["WEB_URL"] == "https://plane.example.test"
    assert environment["CORS_ALLOWED_ORIGINS"] == "https://plane.example.test"


def plane_restore_environment_renders_the_canonical_hostname():
    restore_tasks = yaml.safe_load(read("ansible/roles/plane/tasks/restore.yml"))

    def find_task(value, name):
        if isinstance(value, dict):
            if value.get("name") == name:
                return value
            for child in value.values():
                found = find_task(child, name)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_task(child, name)
                if found is not None:
                    return found
        return None

    render_task = find_task(
        restore_tasks, "Plane restore | Render the target environment with restored keys"
    )
    assert render_task is not None, "restore task does not render the target environment"
    plane_fqdn = render(
        render_task["vars"]["plane_fqdn"],
        {
            "app_config": {"routing": {"subdomain": "plane"}},
            "restore_target": "plane",
            "homelabinfra_infra": {"domain": "example.test"},
        },
    )
    assert plane_fqdn == "plane.example.test", repr(plane_fqdn)

    output = render(
        read("ansible/roles/plane/templates/plane.env.j2"),
        plane_environment_context(plane_fqdn),
    )
    environment = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in output.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert environment["APP_DOMAIN"] == "plane.example.test"
    assert environment["WEB_URL"] == "https://plane.example.test"
    assert environment["CORS_ALLOWED_ORIGINS"] == "https://plane.example.test"


def plane_restore_snapshot_extraction_uses_task_level_options():
    restore_tasks = yaml.safe_load(read("ansible/roles/plane/tasks/restore.yml"))

    def find_task(value, name):
        if isinstance(value, dict):
            if value.get("name") == name:
                return value
            for child in value.values():
                found = find_task(child, name)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_task(child, name)
                if found is not None:
                    return found
        return None

    task = find_task(restore_tasks, "Plane restore | Extract the selected PBS snapshot")
    assert task is not None, "restore task does not extract the selected PBS snapshot"
    command = task["ansible.builtin.command"]
    assert set(command) == {"argv"}, "command module options must stay under argv"
    assert set(task["environment"]) == {
        "PBS_REPOSITORY",
        "PBS_FINGERPRINT",
        "PBS_PASSWORD",
        "PBS_SNAPSHOT",
        "PBS_KEY_URL",
        "PBS_APT_REPO",
    }
    assert task["no_log"] is True
    assert task["changed_when"] is True


def plane_backup_maps_root_only_database_and_redis_config_for_containers():
    script = read("ansible/roles/plane/files/plane-recovery")
    mappings = {
        "DB_HOST": "PLANE_DB_HOST",
        "DB_PORT": "PLANE_DB_PORT",
        "DB_NAME": "PLANE_DB_NAME",
        "DB_USER": "PLANE_DB_USER",
        "DB_PASSWORD": "PLANE_DB_PASSWORD",
        "REDIS_HOST": "PLANE_REDIS_HOST",
        "REDIS_PORT": "PLANE_REDIS_PORT",
        "REDISCLI_AUTH": "PLANE_REDIS_PASSWORD",
    }

    for container_var, config_var in mappings.items():
        assert f'export {container_var}="${config_var}"' in script
        assert f"--env {container_var}" in script
        assert f"--env {container_var}=" not in script


def plane_compose_provides_the_proxy_upstream_aliases():
    template = read("ansible/roles/plane/templates/docker-compose.yml.j2")
    output = render(
        template,
        {
            "ansible_managed": "Managed by gate test",
            "instance": "plane",
            "plane_env_file": "/opt/plane/plane.env",
            "app_config": {
                "app": {
                    "release": "v1.4.2",
                    "data_path": "/opt/plane/data",
                    "object_storage_path": "/opt/plane/object-storage",
                    "port": 8093,
                }
            },
        },
    )
    compose = yaml.safe_load(output)
    services = compose["services"]

    silo_image = "docker.io/pgsty/silo:RELEASE.2026-09-16T00-00-00Z"
    assert services["plane-minio"]["image"] == silo_image
    assert services["plane-minio-init"]["image"] == silo_image

    # Plane's bundled Caddyfile uses these short upstream names.
    for service, alias in (
        ("plane-web", "web"),
        ("plane-space", "space"),
        ("plane-admin", "admin"),
        ("plane-live", "live"),
        ("plane-api", "api"),
    ):
        assert services[service]["networks"]["default"]["aliases"] == [alias]

    assert set(services["plane-proxy"]["depends_on"]) == {
        "plane-web",
        "plane-space",
        "plane-admin",
        "plane-live",
        "plane-api",
    }
    assert "default" in compose["networks"]


def plane_health_probes_do_not_follow_the_unwired_https_hostname():
    accepted_statuses = [200, 301, 302, 303, 307, 308]

    def find_task(value, name):
        if isinstance(value, dict):
            if value.get("name") == name:
                return value
            for child in value.values():
                found = find_task(child, name)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_task(child, name)
                if found is not None:
                    return found
        return None

    for path, task_name, register in (
        (
            "ansible/roles/plane/tasks/main.yml",
            "Plane | Wait for the recovery unit health endpoint",
            "_plane_health",
        ),
        (
            "ansible/roles/plane/tasks/restore.yml",
            "Plane restore | Wait for the restored Plane health endpoint",
            "_plane_restore_health",
        ),
    ):
        task = find_task(yaml.safe_load(read(path)), task_name)
        assert task is not None, f"{path} has no {task_name} task"
        request = task["ansible.builtin.uri"]
        assert request["follow_redirects"] == "none"
        assert request["status_code"] == accepted_statuses
        assert task["until"] == (
            f"{register}.status | default(0) in "
            "[200, 301, 302, 303, 307, 308]"
        )


if __name__ == "__main__":
    caddy = CaddyRendering()
    caddy.setup()
    caddy_default_empty_acme_ca_renders_guest_dns_and_preserves_other_config(caddy)
    caddy_configured_ca_reaches_every_acme_issuer_and_keeps_email(caddy)
    caddy_internal_mode_ignores_configured_ca(caddy)
    caddy_declared_lab_dns_reaches_guest_and_disabled_propagation_policy(caddy)
    caddy_public_dns_renders_each_address_source_and_provider(caddy)
    caddy_explicit_estate_options_are_isolated_and_provider_fields_preserved(caddy)
    caddy_infrastructure_overrides_legacy_instance_resolvers_only_for_default_policy(
        caddy
    )
    caddy_missing_or_invalid_resolvers_fail_without_external_fallback(caddy)
    plane_env_matches_the_bundled_proxy_and_api_contract()
    print("Template rendering: Plane proxy hostname, bucket routing and API worker settings passed")
    plane_restore_environment_renders_the_canonical_hostname()
    print("Template rendering: Plane restore environment renders the canonical hostname")
    plane_restore_snapshot_extraction_uses_task_level_options()
    print("Template rendering: Plane restore extraction keeps task options at task scope")
    plane_backup_maps_root_only_database_and_redis_config_for_containers()
    print(
        "Template rendering: Plane backup maps database and Redis credentials to client containers"
    )
    plane_compose_provides_the_proxy_upstream_aliases()
    print("Template rendering: Plane Compose provides the bundled Caddy upstream aliases")
    plane_health_probes_do_not_follow_the_unwired_https_hostname()
    print("Template rendering: Plane deploy and restore probes keep canonical redirects local")
    emby_rendering()
    unpackerr_rendering()
    navidrome_rendering()
    maintainerr_rendering()
    mautic_rendering()
    mail_consumers_open_smtp_egress()
    flaresolverr_rendering()
    immich_machine_learning_uses_only_a_declared_gpu_device()
    comfyui_accelerator_profile_controls_gpu_runtime()
    immich_task_defaults_missing_gpu_device_to_empty()
    print("Template rendering: Immich and ComfyUI CPU modes omit GPU requests while hardware modes keep their declared devices")
    wireguard_opens_its_udp_forward_before_deploy()
    wireguard_remove_preserves_vaultwarden_configuration()
    wireguard_peer_address_uses_the_ipaddr_filter()
    apps_open_access_through_the_shared_task()
    qbittorrent_enforces_peer_ports_before_start()
    print("Template rendering: Caddy, Emby, Unpackerr, Navidrome, Maintainerr, Mautic, FlareSolverr, qBittorrent startup order, the shared firewall access and mail egress passed")
    
    k3s_defaults_carry_no_lab_topology()
    k3s_template_supports_a_per_node_snapshotter()
    vm_clone_keeps_recorded_app_tags_and_notes()
    bazarr_wiring_sends_lowercase_booleans()
    backup_prune_keeps_the_previous_point()
    k3s_pbs_backup_jobs_read_the_rotated_fingerprint()
    litellm_recovery_jobs_use_the_database_client_major()
    print("Template rendering: Caddy, Emby, Unpackerr, Navidrome, Maintainerr, Mautic, Bazarr, k3s defaults, VM clone tags, backup pruning and LiteLLM recovery clients passed")
