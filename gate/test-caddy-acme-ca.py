#!/usr/bin/env python3
"""Focused rendering checks for Caddy's optional ACME directory."""
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

    def render_policies(self, app: dict[str, object]) -> list[dict[str, object]]:
        policies: list[dict[str, object]] = []
        for estate in self.estates:
            variables: dict[str, object] = {
                "app_config": {"app": app},
                "item": estate,
                "_caddy_dns_policies": policies,
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

    def test_default_empty_acme_ca_renders_the_existing_config_bytes(self):
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
                                                    "1.1.1.1:53",
                                                    "1.0.0.1:53",
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
                                                    "1.1.1.1:53",
                                                    "1.0.0.1:53",
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


if __name__ == "__main__":
    unittest.main()
