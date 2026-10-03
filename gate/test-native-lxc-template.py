#!/usr/bin/env python3
"""Render real native LXC merge seams without a provider, guest or runtime config."""
from copy import deepcopy
from pathlib import Path
import unittest

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
FALLBACK = "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst"
LAB_TEMPLATE = "fixture-shared:vztmpl/debian-13-standard_13.1-1_amd64.tar.zst"
INSTANCE_TEMPLATE = "fixture-instance:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst"
APPS = (
    "caddy", "vaultwarden", "ntfy", "postgresql", "mariadb", "mysql",
    "influxdb", "redis", "wireguard",
)


def load(relative):
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def render(expression, variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def flatten(tasks):
    for task in tasks:
        yield task
        for branch in ("block", "rescue", "always"):
            yield from flatten(task.get(branch, []))


class NativeLxcTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = load("ansible/vars/homelabinfra-defaults.yml")["homelabinfra_defaults"]
        cls.platform_merge = next(
            task["ansible.builtin.set_fact"]["homelabinfra_config"]
            for task in load("ansible/tasks/load-user-vars.yml")
            if task.get("name") == "Merge config layers into homelabinfra_config"
        )
        creation = load("ansible/tasks/proxmox/lxc-create.yml")
        cls.instance_merge = next(
            task["ansible.builtin.set_fact"]["homelabinfra_instance"]
            for task in creation
            if task.get("name") == "Build facts for usage in proxmox lxc creation"
        )
        cls.module_task = next(
            task for task in creation
            if "lxc_module_args" in task.get("ansible.builtin.set_fact", {})
        )

    def resolve(self, app, lab_template=None, instance_template=None, instance_present=True):
        """Follow loader -> app merge -> provisioning overlay -> shared module args."""
        authored = {
            "proxmox": {
                "node": "fixture-node", "api_host": "192.0.2.10",
                "api_token_id": "fixture-id", "api_token_secret": "fixture-secret",
                "storage": "fixture-rootfs", "lxc": {"onboot": True},
                "vm": {"fixture_sibling": "preserve"},
            },
            "ansible": {"ssh_public_key": "fixture-key"},
        }
        if lab_template is not None:
            authored["proxmox"]["lxc"]["ostemplate"] = lab_template
        platform = render(self.platform_merge, {
            "homelabinfra_defaults": self.defaults, "_config_proxmox": authored,
            "_config_infrastructure": {"domain": "example.test"},
        })
        initial = deepcopy(platform)
        instance = f"{app}-fixture"
        variables = {
            "homelabinfra_config": platform, "instance": instance,
            "_app_defaults": load(f"ansible/vars/app-defaults/{app}.yml"),
            "homelabinfra_vault": {},
        }
        if instance_present:
            # An unrelated instance sizing override must not disturb the lab template.
            variables["_instance_config"] = {"proxmox": {"memory": 768}}
            if instance_template is not None:
                variables["_instance_config"]["proxmox"]["ostemplate"] = instance_template
        play = load(f"ansible/playbooks/apps/{app}.yml")[0]
        variables["app_config"] = render(next(
            task["ansible.builtin.set_fact"]["app_config"]
            for task in play["pre_tasks"]
            if "app_config" in task.get("ansible.builtin.set_fact", {})
        ), variables)
        identity = next(
            task["ansible.builtin.set_fact"]["homelabinfra_config"]
            for task in flatten(play["tasks"])
            if "app_config.proxmox" in task.get("ansible.builtin.set_fact", {}).get(
                "homelabinfra_config", ""
            )
        )
        platform = render(identity, variables)
        self.assertEqual(platform["ansible"], initial["ansible"])
        self.assertEqual(platform["infrastructure"], initial["infrastructure"])
        for key in ("node", "api_host", "storage", "vm"):
            self.assertEqual(platform["proxmox"][key], initial["proxmox"][key])
        self.assertTrue(platform["proxmox"]["lxc"]["onboot"])
        self.assertEqual(platform["proxmox"]["lxc"]["hostname"], instance)
        self.assertIn("_" + instance, platform["proxmox"]["lxc"]["tags"])
        self.assertEqual(platform["proxmox"]["lxc"]["memory"],
                         variables["app_config"]["proxmox"]["memory"])
        runtime = {"network": {"ip_address": "192.0.2.20"}, "fixture_sibling": "preserve"}
        variables.update(homelabinfra_config=platform, homelabinfra_instance=runtime)
        runtime = render(self.instance_merge, variables)
        self.assertEqual(runtime["network"]["ip_address"], "192.0.2.20")
        self.assertEqual(runtime["fixture_sibling"], "preserve")
        variables.update(homelabinfra_instance=runtime, **self.module_task["vars"])
        args = render(self.module_task["ansible.builtin.set_fact"]["lxc_module_args"], variables)
        self.assertEqual(args["ostemplate"], platform["proxmox"]["lxc"]["ostemplate"])
        return args["ostemplate"]

    def test_authored_shared_debian_13_survives_without_instance_file(self):
        for app in APPS:
            with self.subTest(app=app):
                self.assertEqual(self.resolve(app, LAB_TEMPLATE, instance_present=False), LAB_TEMPLATE)

    def test_authored_shared_debian_13_survives_unrelated_instance_override(self):
        for app in APPS:
            with self.subTest(app=app):
                self.assertEqual(self.resolve(app, LAB_TEMPLATE), LAB_TEMPLATE)

    def test_explicit_instance_template_wins(self):
        for app in APPS:
            for template in (INSTANCE_TEMPLATE, FALLBACK):
                with self.subTest(app=app, template=template):
                    self.assertEqual(self.resolve(app, LAB_TEMPLATE, template), template)

    def test_repository_fallback_without_authored_or_instance_template(self):
        self.assertEqual(self.defaults["proxmox"]["lxc"]["ostemplate"], FALLBACK)
        for app in APPS:
            with self.subTest(app=app):
                self.assertEqual(self.resolve(app, instance_present=False), FALLBACK)

    def test_explicit_instance_template_without_authored_lab_template(self):
        for app in APPS:
            with self.subTest(app=app):
                self.assertEqual(self.resolve(app, instance_template=INSTANCE_TEMPLATE), INSTANCE_TEMPLATE)

    def test_app_defaults_and_copy_template_do_not_pin_ostemplate(self):
        for path in (ROOT / "ansible/vars/app-defaults").glob("*.yml"):
            for defaults in yaml.safe_load(path.read_text(encoding="utf-8")).values():
                with self.subTest(path=path.name):
                    self.assertNotIn("ostemplate", defaults.get("proxmox", {}))


if __name__ == "__main__":
    unittest.main()
