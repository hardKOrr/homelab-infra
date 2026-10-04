#!/usr/bin/env python3
"""Provider-free checks for Caddy ACME directory and DNS resolver resolution."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_FILE = ROOT / "ansible/vars/app-defaults/caddy.yml"
ROLE_FILE = ROOT / "ansible/roles/caddy/tasks/main.yml"
EXAMPLE_FILE = ROOT / "config.example/apps/caddy.example.yml"
STAGING_DIRECTORY = "https://acme-staging-v02.api.letsencrypt.org/directory"


def render(expression: str, variables: dict[str, object]) -> object:
    """Render a value using Ansible's own Jinja filters and native result handling."""
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def task_named(tasks: list[dict[str, object]], name: str) -> dict[str, object]:
    return next(task for task in tasks if task.get("name") == name)


class CaddyAcmeCaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8"))[
            "caddy_defaults"
        ]
        cls.tasks = yaml.safe_load(ROLE_FILE.read_text(encoding="utf-8"))
        cls.policy_task = task_named(cls.tasks, "Build per-estate DNS-challenge TLS policies")
        cls.base_task = task_named(cls.tasks, "Build the desired base configuration")
        cls.write_task = task_named(cls.tasks, "Write the base configuration")
        cls.policy_expression = cls.policy_task["ansible.builtin.set_fact"][
            "_caddy_dns_policies"
        ]
        cls.base_expression = cls.base_task["ansible.builtin.set_fact"][
            "_caddy_base_config"
        ]
        cls.resolver_task = task_named(cls.tasks, "Collect DNS-challenge resolver defaults")
        cls.validation_task = task_named(cls.tasks, "Validate DNS-challenge resolver path")
        cls.collect_task = task_named(cls.tasks, "Collect DNS-challenge domains")
        cls.policy_vars = cls.policy_task["vars"]
        cls.base_vars = cls.base_task["vars"]
        cls.content_expression = cls.write_task["ansible.builtin.copy"]["content"]
        cls.estates = [
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
            {"ansible_facts": facts if facts is not None else {
                "dns": {"nameservers": ["192.0.2.53", "2001:db8::53"]}
            }},
        )

    def collect_domains(self, infra, legacy):
        variables = {"homelabinfra_config": {"infrastructure": infra},
                     "app_config": {"app": {"dns_challenge": legacy}}}
        for key, expression in self.collect_task["vars"].items():
            variables[key] = render(expression, variables)
        return render(self.collect_task["ansible.builtin.set_fact"]["_caddy_dns_domains"], variables)

    def resolvers_valid(self, challenge, facts=None):
        variables = {"_caddy_dns_domains": [{"dns_challenge": {
            "api_token": "fixture-token-validation", **challenge}}],
                     "_caddy_default_resolvers": self.default_resolvers(facts)}
        loop = render(self.validation_task["loop"], variables)
        self.assertEqual(len(loop), 1)
        self.assertNotIn("fixture-token-validation", str(loop))
        variables["item"] = loop[0]
        for key, expression in self.validation_task["vars"].items():
            variables[key] = render(expression, variables)
        return all(render("{{ " + condition + " }}", variables)
                   for condition in self.validation_task["ansible.builtin.assert"]["that"])

    def render_policies(self, app: dict[str, object], *, estates=None, facts=None) -> list[dict[str, object]]:
        policies: list[dict[str, object]] = []
        for estate in (self.estates if estates is None else estates):
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

    def render_config(self, app: dict[str, object]) -> str:
        policies = self.render_policies(app)
        variables: dict[str, object] = {
            "app_config": {"app": app},
            "_caddy_dns_policies": policies,
            "_caddy_automate_names": [
                "*.alpha.example.test",
                "*.beta.example.test",
            ],
            "_caddy_bind_ip": "192.0.2.10",
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

    def test_default_empty_acme_ca_renders_guest_dns_and_preserves_other_config(self):
        self.assertEqual(self.defaults["app"]["acme_ca"], "")
        output = self.render_config(self.app_config())
        expected = {
            "admin": {"listen": "192.0.2.10:2019"},
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443", ":80"],
                            "routes": [],
                        }
                    }
                },
                "tls": {
                    "automation": {
                        "policies": [
                            {
                                "subjects": [
                                    "*.alpha.example.test",
                                    "alpha.example.test",
                                ],
                                "issuers": [
                                    {
                                        "module": "acme",
                                        "challenges": {
                                            "dns": {
                                                "provider": {
                                                    "name": "cloudflare",
                                                    "api_token": "fixture-token-alpha",
                                                },
                                                "resolvers": [
                                                    "192.0.2.53",
                                                    "2001:db8::53",
                                                ],
                                                "propagation_delay": "45s",
                                                "propagation_timeout": -1,
                                            }
                                        },
                                        "email": "ops@example.test",
                                    }
                                ],
                            },
                            {
                                "subjects": [
                                    "*.beta.example.test",
                                    "beta.example.test",
                                ],
                                "issuers": [
                                    {
                                        "module": "acme",
                                        "challenges": {
                                            "dns": {
                                                "provider": {
                                                    "name": "cloudflare",
                                                    "api_token": "fixture-token-beta",
                                                },
                                                "resolvers": [
                                                    "192.0.2.53",
                                                    "2001:db8::53",
                                                ],
                                                "propagation_delay": "30s",
                                                "propagation_timeout": -1,
                                            }
                                        },
                                        "email": "ops@example.test",
                                    }
                                ],
                            },
                            {
                                "issuers": [
                                    {"module": "acme", "email": "ops@example.test"}
                                ]
                            },
                        ]
                    },
                    "certificates": {
                        "automate": [
                            "*.alpha.example.test",
                            "*.beta.example.test",
                        ]
                    },
                },
            },
        }
        expected_bytes = render("{{ baseline | to_nice_json }}", {"baseline": expected})
        self.assertEqual(output, expected_bytes)

    def test_configured_ca_reaches_every_acme_issuer_and_keeps_email(self):
        self.assertIn(STAGING_DIRECTORY, EXAMPLE_FILE.read_text(encoding="utf-8"))
        config = json.loads(self.render_config(self.app_config(ca=STAGING_DIRECTORY)))
        policies = config["apps"]["tls"]["automation"]["policies"]
        for policy in policies:
            issuer = policy["issuers"][0]
            self.assertEqual(issuer["module"], "acme")
            self.assertEqual(issuer["ca"], STAGING_DIRECTORY)
            self.assertEqual(issuer["email"], "ops@example.test")

    def test_internal_mode_ignores_configured_ca(self):
        internal_bytes = self.render_config(
            self.app_config(ca=STAGING_DIRECTORY, tls_mode="internal")
        )
        same_internal_without_ca = self.render_config(
            self.app_config(tls_mode="internal")
        )
        self.assertEqual(internal_bytes, same_internal_without_ca)
        internal = json.loads(internal_bytes)
        policies = internal["apps"]["tls"]["automation"]["policies"]
        for policy in policies[:-1]:
            issuer = policy["issuers"][0]
            self.assertNotIn("ca", issuer)
            self.assertEqual(issuer["email"], "ops@example.test")
        self.assertEqual(policies[-1]["issuers"], [{"module": "internal"}])

    def test_declared_lab_dns_reaches_guest_and_disabled_propagation_policy(self):
        tasks = yaml.safe_load((ROOT / "ansible/tasks/network/resolve-network.yml").read_text())
        expression = task_named(tasks, "Resolve network | Build the effective config for that network")[
            "ansible.builtin.set_fact"]["network_effective"]
        config = {"networks": {"default": {
            "dns_servers": ["192.0.2.53"], "searchdomain": "example.test",
        }, "shared": {"ip_offset": 10}}}
        for shared in ({"ip_offset": 10}, {"dns_servers": ["192.0.2.54", "2001:db8::54"]}):
            with self.subTest(shared=shared):
                config["networks"]["shared"] = shared
                network = render(expression, {
                    "homelabinfra_config": config, "network_selected": "shared"})
                create_tasks = yaml.safe_load((ROOT / "ansible/tasks/proxmox/lxc-create.yml").read_text())
                module_task = next(t for t in create_tasks if "lxc_netif" in str(t.get("ansible.builtin.set_fact", ""))
                                   and "nameserver" in str(t.get("ansible.builtin.set_fact", "")))
                guest = render(module_task["ansible.builtin.set_fact"]["homelabinfra_instance"], {
                    "homelabinfra_instance": {"network": network, "lxc": {"memory": 512}},
                    "lxc_netif": "name=eth0,bridge=vmbr0,ip=192.0.2.10/24"})
                self.assertEqual(guest["lxc"]["memory"], 512)
                # Model the gathered resolv.conf facts seeded by the real LXC args.
                nameservers = guest["lxc"]["nameserver"].split()
                self.assertEqual(nameservers, network["dns_servers"])
                facts = {"dns": {"nameservers": nameservers}}
                for policy in self.render_policies(self.app_config(ca=STAGING_DIRECTORY), facts=facts):
                    dns = policy["issuers"][0]["challenges"]["dns"]
                    self.assertEqual(dns["resolvers"], nameservers)
                    self.assertEqual(dns["propagation_timeout"], -1)
                    self.assertEqual(policy["issuers"][0]["ca"], STAGING_DIRECTORY)

    def test_explicit_estate_options_are_isolated_and_provider_fields_preserved(self):
        estates = self.collect_domains({"domains": {
            "alpha": {"domain": "alpha.example.test", "dns_challenge": {
                "provider": "route53", "access_key_id": "fixture-access-id",
                "secret_access_key": "fixture-secret-key", "region": "fixture-region",
                "resolvers": ["192.0.2.60:5353", "[2001:db8::60]:53"],
                "propagation_timeout": -1, "propagation_delay": "10s",
                "ttl": "60s", "override_domain": "validation.example.test"}},
            "beta": self.estates[1],
        }}, {"resolvers": ["192.0.2.99:53"]})
        policies = self.render_policies(self.app_config(), estates=estates)
        alpha, beta = [p["issuers"][0]["challenges"]["dns"] for p in policies]
        self.assertEqual(alpha, {
            "provider": {"name": "route53", "access_key_id": "fixture-access-id",
                         "secret_access_key": "fixture-secret-key", "region": "fixture-region"},
            "resolvers": ["192.0.2.60:5353", "[2001:db8::60]:53"],
            "propagation_timeout": -1, "propagation_delay": "10s",
            "ttl": "60s", "override_domain": "validation.example.test"})
        self.assertEqual(beta["resolvers"], self.default_resolvers())
        self.assertEqual(beta["provider"], {"name": "cloudflare", "api_token": "fixture-token-beta"})
        self.assertEqual(beta["propagation_delay"], "30s")

    def test_infrastructure_overrides_legacy_instance_resolvers_only_for_default_policy(self):
        legacy = {"provider": "cloudflare", "api_token": "fixture-token-legacy",
                  "resolvers": ["192.0.2.61:53"], "propagation_delay": "40s"}
        infra = {"domain": "default.example.test", "reverse_proxy": {"dns_challenge": {
            "resolvers": ["192.0.2.62:53"], "propagation_timeout": "2m"}}}
        for overrides, expected in (({}, legacy["resolvers"]),
                                    (infra["reverse_proxy"], ["192.0.2.62:53"])):
            domains = self.collect_domains({**infra, "reverse_proxy": overrides}, legacy)
            dns = self.render_policies(self.app_config(), estates=domains)[0]["issuers"][0]["challenges"]["dns"]
            self.assertEqual(dns["resolvers"], expected)
            self.assertEqual(dns["provider"]["api_token"], "fixture-token-legacy")
            self.assertEqual(dns["propagation_delay"], "40s")
            self.assertEqual(dns["propagation_timeout"], "2m" if overrides else -1)

    def test_missing_or_invalid_resolvers_fail_without_external_fallback(self):
        for facts in ({}, {"dns": {"nameservers": []}}):
            with self.subTest(facts=facts):
                self.assertFalse(self.resolvers_valid({}, facts))
                self.assertTrue(self.resolvers_valid({"resolvers": ["192.0.2.53:53"]}, facts))
        self.assertTrue(self.resolvers_valid({}))
        for invalid in ([], "192.0.2.53", {}, None, [None], [""]):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.resolvers_valid({"resolvers": invalid}))
        for task in (self.collect_task, self.policy_task):
            self.assertTrue(task["no_log"])
        self.assertFalse(self.validation_task.get("no_log", False))


if __name__ == "__main__":
    unittest.main()
