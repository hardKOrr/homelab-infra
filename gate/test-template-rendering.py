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

    def render_config(self, app: dict[str, object]) -> str:
        policies = self.render_policies(app)
        variables: dict[str, object] = {
            "app_config": {"app": app},
            "_caddy_dns_policies": policies,
            "_caddy_automate_names": ["*.alpha.example.test", "*.beta.example.test"],
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
    mount_guard = next(
        (
            task["ansible.builtin.assert"]["that"]
            for task in role_tasks
            if task.get("name")
            == "Assert each Emby library is inside a declared media mount"
        )
    )
    guard_template = mount_guard
    mounted = render(
        guard_template,
        {
            "item": {"_root": "/srv/fixtures/media", "subpath": "movies"},
            "_emby_mounts": [{"path": "/srv/fixtures/media"}],
        },
    )
    unmounted = render(
        guard_template,
        {
            "item": {"_root": "/srv/fixtures/media", "subpath": "movies"},
            "_emby_mounts": [{"path": "/srv/fixtures/other"}],
        },
    )
    assert str(mounted).strip().lower() == "true", "mounted is True"
    assert str(unmounted).strip().lower() == "false", "unmounted is False"


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
    mount_guard = next(
        (
            task["ansible.builtin.assert"]["that"]
            for task in role_tasks
            if task.get("name")
            == "Assert Navidrome music path is inside a declared media mount"
        )
    )
    guard_template = mount_guard
    mounted = render(
        guard_template,
        {
            "_navidrome_music_path": "/srv/fixtures/media/music",
            "_navidrome_mounts": [{"path": "/srv/fixtures/media"}],
        },
    )
    unmounted = render(
        guard_template,
        {
            "_navidrome_music_path": "/srv/fixtures/other/music",
            "_navidrome_mounts": [{"path": "/srv/fixtures/media"}],
        },
    )
    assert str(mounted).strip().lower() == "true", "mounted is True"
    assert str(unmounted).strip().lower() == "false", "unmounted is False"


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


if __name__ == "__main__":
    caddy = CaddyRendering()
    caddy.setup()
    caddy_default_empty_acme_ca_renders_guest_dns_and_preserves_other_config(caddy)
    caddy_configured_ca_reaches_every_acme_issuer_and_keeps_email(caddy)
    caddy_internal_mode_ignores_configured_ca(caddy)
    caddy_declared_lab_dns_reaches_guest_and_disabled_propagation_policy(caddy)
    caddy_explicit_estate_options_are_isolated_and_provider_fields_preserved(caddy)
    caddy_infrastructure_overrides_legacy_instance_resolvers_only_for_default_policy(
        caddy
    )
    caddy_missing_or_invalid_resolvers_fail_without_external_fallback(caddy)
    emby_rendering()
    unpackerr_rendering()
    navidrome_rendering()
    maintainerr_rendering()
    mautic_rendering()
    mail_consumers_open_smtp_egress()
    print("Template rendering: Caddy, Emby, Unpackerr, Navidrome, Maintainerr, Mautic and mail egress passed")
