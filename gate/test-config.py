#!/usr/bin/env python3
"""Configuration precedence, estate isolation, provider gates and network selection."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

repo = Path(__file__).resolve().parents[1]
fixtures = repo / "gate/fixtures/config-loading"
os.environ["ANSIBLE_CONFIG"] = str(repo / "ansible/ansible.cfg")


def render(expr, **ctx):
    return Templar(loader=DataLoader(), variables=ctx).template(expr)


def check(label, got, want):
    assert got == want, f"{label}: expected {want!r}, got {got!r}"


def configuration_layers_and_estates():
    SET_FACT = ("ansible.builtin.set_fact", "set_fact")
    ASSERT = ("ansible.builtin.assert", "assert")

    def load_tasks(relpath):
        return yaml.safe_load((repo / relpath).read_text(encoding="utf-8"))

    def find_task(tasks, name):
        for task in tasks:
            if task.get("name") == name:
                return task
        raise SystemExit(
            "%s: no task named %r -- it moved or was renamed; update this test to read it where it now lives"
            % (name, name)
        )

    def find_play_task(plays, play_name, task_name):
        for play in plays:
            if play.get("name") == play_name:
                return find_task(play["tasks"], task_name)
        raise SystemExit("no play named %r" % play_name)

    def module_body(task, module_names):
        for key in module_names:
            if key in task:
                return task[key]
        raise SystemExit(
            "task %r declares none of %s" % (task.get("name"), module_names)
        )

    def render_vars_in_order(vars_map, ctx):
        """Ansible resolves `vars:` lazily in declaration order; iterate and accumulate,
        for each declared variable."""
        rendered = dict(ctx)
        for name, expr in vars_map.items():
            rendered[name] = render(expr, **rendered)
        return rendered

    luv_tasks = load_tasks("ansible/tasks/load-user-vars.yml")
    merge_task = find_task(luv_tasks, "Merge config layers into homelabinfra_config")
    merge_expr = module_body(merge_task, SET_FACT)["homelabinfra_config"]
    defaults = {
        "infrastructure": {"backups": {"schedule": "daily"}},
        "proxmox": {"api_port": 8006, "storage": "local-lvm"},
    }
    config_proxmox = {"proxmox": {"node": "pve", "api_port": 8007}}
    config_infrastructure = {
        "domain": "lab.example",
        "backups": {"datastore_path": "/mnt/backup"},
    }
    user_vars_override = {"proxmox": {"node": "pve-override"}}
    result = render(
        merge_expr,
        homelabinfra_defaults=defaults,
        _config_proxmox=config_proxmox,
        _config_infrastructure=config_infrastructure,
        homelabinfra_config={},
    )
    check(
        "proxmox.yml overrides only api_port, node is unset by defaults",
        result["proxmox"]["node"],
        "pve",
    )
    check(
        "proxmox.yml's api_port wins over the defaults seed",
        result["proxmox"]["api_port"],
        8007,
    )
    check(
        "a per-key recursive merge keeps the defaults' proxmox.storage even though config_proxmox's proxmox mapping does not repeat it",
        result["proxmox"]["storage"],
        "local-lvm",
    )
    check(
        "infrastructure.yml's domain lands under .infrastructure",
        result["infrastructure"]["domain"],
        "lab.example",
    )
    check(
        "recursive merge keeps the defaults' backups.schedule alongside infrastructure.yml's backups.datastore_path",
        result["infrastructure"]["backups"],
        {"schedule": "daily", "datastore_path": "/mnt/backup"},
    )
    result_override = render(
        merge_expr,
        homelabinfra_defaults=defaults,
        _config_proxmox=config_proxmox,
        _config_infrastructure=config_infrastructure,
        homelabinfra_config=user_vars_override,
    )
    check(
        "the highest-precedence layer (user_vars_file/env) wins over config/proxmox.yml",
        result_override["proxmox"]["node"],
        "pve-override",
    )
    result_absent = render(
        merge_expr, homelabinfra_defaults=defaults, homelabinfra_config={}
    )
    check(
        "an absent config/proxmox.yml and config/infrastructure.yml still resolve, seeded only from defaults",
        result_absent["proxmox"],
        {"api_port": 8006, "storage": "local-lvm"},
    )
    env_task = find_task(luv_tasks, "Overlay secrets supplied through the environment")
    env_expr = module_body(env_task, SET_FACT)["homelabinfra_config"]
    env_vars = env_task["vars"]
    file_shaped_config = {
        "proxmox": {"api_token_secret": "from-file-not-a-real-secret"}
    }
    os.environ.pop("PROXMOX_API_TOKEN", None)
    os.environ.pop("PROXMOX_API_TOKEN_ID", None)
    os.environ.pop("PROXMOX_API_USER", None)
    os.environ.pop("VAULTWARDEN_ADMIN_TOKEN", None)
    os.environ.pop("CLOUDFLARE_API_TOKEN", None)
    ctx_absent = render_vars_in_order(
        env_vars, {"homelabinfra_config": file_shaped_config}
    )
    result_env_absent = render(env_expr, **ctx_absent)
    check(
        "an absent PROXMOX_API_TOKEN leaves the file-supplied secret untouched",
        result_env_absent["proxmox"]["api_token_secret"],
        "from-file-not-a-real-secret",
    )
    os.environ["PROXMOX_API_TOKEN"] = "from-env-not-a-real-secret"
    try:
        ctx_present = render_vars_in_order(
            env_vars, {"homelabinfra_config": file_shaped_config}
        )
        result_env_present = render(env_expr, **ctx_present)
    finally:
        del os.environ["PROXMOX_API_TOKEN"]
    check(
        "PROXMOX_API_TOKEN from the environment overlays LAST and wins over the file value",
        result_env_present["proxmox"]["api_token_secret"],
        "from-env-not-a-real-secret",
    )
    estate_tasks = load_tasks("ansible/tasks/resolve-estate.yml")
    overlay_task = find_task(
        estate_tasks, "Resolve estate | Overlay estate-scoped facts"
    )
    overlay_expr = module_body(overlay_task, SET_FACT)["homelabinfra_infra"]
    overlay_vars = overlay_task["vars"]
    overlay_when = overlay_task["when"]
    homelabinfra_infra = {
        "domain": "personal.fixture.invalid",
        "sso": {
            "provider": "authentik",
            "instance": "authentik",
            "admin_user": "akadmin",
            "admin_password": "personal-secret",
        },
        "dns": {
            "provider": "opnsense",
            "host": "198.51.100.1",
            "api_secret": "personal-dns-secret",
        },
        "estates": {"foxglove": {"sso": {"provider": "none"}}},
    }
    estate_domains = {
        "personal": {"domain": "personal.fixture.invalid", "default": True},
        "foxglove": {"domain": "foxglove.fixture.invalid", "network": "foxglove"},
    }

    def overlay_for(infra, selected, default):
        base_ctx = {
            "homelabinfra_infra": infra,
            "_estate_domains": estate_domains,
            "_estate_selected": selected,
            "_estate_default": default,
        }
        ctx = render_vars_in_order(overlay_vars, base_ctx)
        fires = all((render("{{ %s }}" % cond, **ctx) for cond in overlay_when))
        result = render(overlay_expr, **ctx) if fires else infra
        return (result, fires)

    overlaid, fired = overlay_for(homelabinfra_infra, "foxglove", "personal")
    check(
        "the overlay's own when: conditions fire for a genuine non-default estate",
        fired,
        True,
    )
    check(
        "a non-default estate takes its own domain",
        overlaid["domain"],
        "foxglove.fixture.invalid",
    )
    check(
        "a non-default estate's sso is replaced WHOLE, not merged",
        overlaid["sso"],
        {"provider": "none"},
    )
    check(
        "the default estate's sso admin_password does not leak into another estate's sso",
        "admin_password" in overlaid["sso"],
        False,
    )
    check(
        "an estate that declares no dns of its own inherits the global dns entry whole",
        overlaid["dns"],
        homelabinfra_infra["dns"],
    )
    _, fired_default = overlay_for(homelabinfra_infra, "personal", "personal")
    check(
        "the overlay is a real no-op (when: false) for the default estate itself",
        fired_default,
        False,
    )
    homelabinfra_infra_with_estate_dns = dict(homelabinfra_infra)
    homelabinfra_infra_with_estate_dns["estates"] = {
        "foxglove": {
            "sso": {"provider": "none"},
            "dns": {"provider": "pihole", "host": "198.51.100.9"},
        }
    }
    overlaid_dns, _ = overlay_for(
        homelabinfra_infra_with_estate_dns, "foxglove", "personal"
    )
    check(
        "an estate with its own dns block replaces the global one whole",
        overlaid_dns["dns"],
        {"provider": "pihole", "host": "198.51.100.9"},
    )
    mail_task = find_task(
        estate_tasks, "Resolve estate | Overlay the estate's authored mail identity"
    )
    mail_expr = module_body(mail_task, SET_FACT)["homelabinfra_infra"]
    mail_vars = mail_task["vars"]
    mail_when = mail_task["when"]
    mail_domains = {
        "personal": {
            "domain": "personal.fixture.invalid",
            "default": True,
            "mail": {
                "from_address": "personal@personal.fixture.invalid",
                "from_name": "Personal",
            },
        },
        "foxglove": {
            "domain": "foxglove.fixture.invalid",
            "mail": {
                "from_address": "hello@foxglove.fixture.invalid",
                "from_name": "Foxglove",
            },
        },
    }
    mail_global = {
        "provider": "smtp",
        "host": "smtp.fixture.invalid",
        "port": 587,
        "encryption": "starttls",
        "from_address": "default@personal.fixture.invalid",
        "from_name": "Default",
        "username": "shared-login",
        "password": "shared-relay-secret",
    }
    mail_infra = {
        "mail": mail_global,
        "estates": {
            "personal": {"mail": {"password": "default-estate-secret"}},
            "foxglove": {},
        },
    }

    def mail_overlay_for(infra, domains, selected, default):
        ctx = {
            "homelabinfra_infra": infra,
            "_estate_domains": domains,
            "_estate_selected": selected,
            "_estate_default": default,
        }
        ctx = render_vars_in_order(mail_vars, ctx)
        fires = all((render("{{ %s }}" % cond, **ctx) for cond in mail_when))
        result = render(mail_expr, **ctx) if fires else infra
        return (result, fires)

    mail_fox, mail_fox_fired = mail_overlay_for(
        mail_infra, mail_domains, "foxglove", "personal"
    )
    check(
        "mail overlay fires for an estate with an authored identity",
        mail_fox_fired,
        True,
    )
    check(
        "mail overlay selects the estate From address",
        mail_fox["mail"]["from_address"],
        "hello@foxglove.fixture.invalid",
    )
    check(
        "mail overlay selects the estate From name",
        mail_fox["mail"]["from_name"],
        "Foxglove",
    )
    check(
        "mail overlay retains the shared relay host and port",
        (mail_fox["mail"]["host"], mail_fox["mail"]["port"]),
        ("smtp.fixture.invalid", 587),
    )
    check(
        "an estate with no recorded mail credential uses the shared relay credential",
        mail_fox["mail"]["password"],
        "shared-relay-secret",
    )
    if mail_fox["mail"]["password"] == "default-estate-secret":
        raise AssertionError(
            "an estate with no recorded mail credential inherited the default estate credential"
        )
    mail_infra_scoped = {
        "mail": mail_global,
        "estates": {
            "personal": {"mail": {"password": "default-estate-secret"}},
            "foxglove": {"mail": {"password": "foxglove-relay-secret"}},
        },
    }
    mail_fox_scoped, _ = mail_overlay_for(
        mail_infra_scoped, mail_domains, "foxglove", "personal"
    )
    check(
        "a recorded estate credential overrides the shared relay credential",
        mail_fox_scoped["mail"]["password"],
        "foxglove-relay-secret",
    )
    if mail_fox_scoped["mail"]["password"] == "default-estate-secret":
        raise AssertionError(
            "foxglove inherited the recorded default estate credential"
        )
    mail_no_declaration_domains = {
        "personal": {"domain": "personal.fixture.invalid", "default": True},
        "foxglove": {"domain": "foxglove.fixture.invalid"},
    }
    mail_fox_unchanged, mail_unchanged_fired = mail_overlay_for(
        mail_infra, mail_no_declaration_domains, "foxglove", "personal"
    )
    check(
        "an estate with no mail block leaves the registry byte-for-byte unchanged",
        mail_unchanged_fired,
        False,
    )
    check(
        "an estate with no mail block keeps the global From address",
        mail_fox_unchanged["mail"]["from_address"],
        mail_global["from_address"],
    )
    check(
        "the default estate ignores an estate-scoped mail credential",
        mail_overlay_for(mail_infra, mail_domains, "personal", "personal")[0]["mail"][
            "password"
        ],
        "shared-relay-secret",
    )
    compute_task = find_task(estate_tasks, "Resolve estate | Compute estate names")
    compute_fields = module_body(compute_task, SET_FACT)
    assert_task = find_task(
        estate_tasks, "Resolve estate | Require one explicit multi-estate default"
    )
    assert_that = module_body(assert_task, ASSERT)["that"]

    def assert_holds(domains):
        ctx = {"_estate_infra_cfg": {"domains": domains}}
        ctx["_estate_domains"] = render(compute_fields["_estate_domains"], **ctx)
        ctx["_estate_defaults"] = render(compute_fields["_estate_defaults"], **ctx)
        conditions = assert_that if isinstance(assert_that, list) else [assert_that]
        return all((render("{{ %s }}" % cond, **ctx) for cond in conditions))

    check(
        "two estates, one default: true -- the real assert condition holds",
        assert_holds(estate_domains),
        True,
    )
    no_default = {
        "personal": {"domain": "personal.fixture.invalid"},
        "foxglove": {"domain": "foxglove.fixture.invalid"},
    }
    check(
        "two estates, neither default: true -- the real assert condition fails (declaration order must not decide)",
        assert_holds(no_default),
        False,
    )
    two_defaults = {
        "personal": {"domain": "personal.fixture.invalid", "default": True},
        "foxglove": {"domain": "foxglove.fixture.invalid", "default": True},
    }
    check(
        "two estates, both default: true -- the real assert condition fails",
        assert_holds(two_defaults),
        False,
    )
    caddy_plays = load_tasks("ansible/playbooks/apps/caddy.yml")
    wire_dns_task = find_play_task(
        caddy_plays, "Caddy | Record facts and wire", "Wire DNS"
    )
    wire_dns_when = wire_dns_task["when"]
    UNDEFINED = object()

    def dns_wiring_fires(homelabinfra_infra_value):
        ctx = (
            {}
            if homelabinfra_infra_value is UNDEFINED
            else {"homelabinfra_infra": homelabinfra_infra_value}
        )
        return all((render("{{ %s }}" % cond, **ctx) for cond in wire_dns_when))

    check("undefined infrastructure is a no-op", dns_wiring_fires(UNDEFINED), False)
    check(
        "homelabinfra_infra defined but .dns absent is a silent no-op",
        dns_wiring_fires({}),
        False,
    )
    check(
        "an explicit dns.provider: none is a silent no-op",
        dns_wiring_fires({"dns": {"provider": "none"}}),
        False,
    )
    check(
        "a configured dns.provider fires the wiring gate",
        dns_wiring_fires({"dns": {"provider": "opnsense"}}),
        True,
    )


def fixture_layers_and_provider_gates():
    loader_file = repo / "ansible" / "tasks" / "load-user-vars.yml"
    tasks = yaml.safe_load(loader_file.read_text(encoding="utf-8"))
    merge_task = next(
        (
            t
            for t in tasks
            if t.get("name") == "Merge config layers into homelabinfra_config"
        ),
        None,
    )
    if merge_task is None:
        raise SystemExit(
            "config-fixtures test: load-user-vars.yml no longer has a 'Merge config layers into homelabinfra_config' task; the merge moved, update this test to read it where it now lives."
        )
    merge_expr = merge_task["ansible.builtin.set_fact"]["homelabinfra_config"]
    defaults_raw = yaml.safe_load(
        (repo / "ansible" / "vars" / "homelabinfra-defaults.yml").read_text(
            encoding="utf-8"
        )
    )
    homelabinfra_defaults = defaults_raw["homelabinfra_defaults"]
    config_proxmox = yaml.safe_load(
        (fixtures / "valid" / "proxmox.yml").read_text(encoding="utf-8")
    )
    config_infrastructure = yaml.safe_load(
        (fixtures / "valid" / "infrastructure.yml").read_text(encoding="utf-8")
    )

    def merged(pre_existing_config=None):
        return render(
            merge_expr,
            homelabinfra_defaults=homelabinfra_defaults,
            _config_proxmox=config_proxmox,
            _config_infrastructure=config_infrastructure,
            homelabinfra_config=pre_existing_config or {},
        )

    result = merged()
    if not homelabinfra_defaults:
        raise AssertionError(
            "vars/homelabinfra-defaults.yml unwrapped to nothing; fixture assumption stale"
        )
    else:
        default_only_keys = [
            k
            for k in homelabinfra_defaults
            if k not in ("proxmox", "networks", "ansible", "infrastructure")
        ]
        for key in default_only_keys:
            check(
                "default-only key %r survives the merge" % key,
                result.get(key),
                homelabinfra_defaults[key],
            )
    check(
        "proxmox.yml -> homelabinfra_config.proxmox.api_host",
        result["proxmox"]["api_host"],
        config_proxmox["proxmox"]["api_host"],
    )
    check(
        "proxmox.yml -> homelabinfra_config.networks.default.cidr",
        result["networks"]["default"]["cidr"],
        config_proxmox["networks"]["default"]["cidr"],
    )
    check(
        "proxmox.yml layering keeps a proxmox.* default the fixture never mentions",
        result["proxmox"]["lxc"]["ostemplate"],
        homelabinfra_defaults["proxmox"]["lxc"]["ostemplate"],
    )
    check(
        "proxmox.yml layering keeps proxmox.api_user's git-managed default untouched by an unrelated fixture key",
        result["proxmox"]["api_port"],
        homelabinfra_defaults["proxmox"]["api_port"],
    )
    check(
        "a partial networks.shared band does not erase a defaulted sibling key",
        result["networks"]["shared"]["ip_offset"],
        config_proxmox["networks"]["shared"]["ip_offset"],
    )
    check(
        "infrastructure.yml -> homelabinfra_config.infrastructure.domain",
        result["infrastructure"]["domain"],
        config_infrastructure["domain"],
    )
    check(
        "infrastructure.yml -> homelabinfra_config.infrastructure.reverse_proxy.provider",
        result["infrastructure"]["reverse_proxy"]["provider"],
        config_infrastructure["reverse_proxy"]["provider"],
    )
    check(
        "infrastructure.yml layering keeps infrastructure.media_storage.owner.uid, a nested default the fixture never mentions",
        result["infrastructure"]["media_storage"]["owner"]["uid"],
        homelabinfra_defaults["infrastructure"]["media_storage"]["owner"]["uid"],
    )
    default_boot_order = homelabinfra_defaults["infrastructure"]["maintenance"]["boot"][
        "order"
    ]
    fixture_boot_order = config_infrastructure["maintenance"]["boot"]["order"]
    if default_boot_order == fixture_boot_order:
        raise AssertionError(
            "fixture assumption stale: infrastructure.yml maintenance.boot.order no longer differs from the git-managed default (%r) -- the override/sibling check below would be vacuous"
            % default_boot_order
        )
    check(
        "infrastructure.yml's maintenance.boot.order overrides the git-managed default",
        result["infrastructure"]["maintenance"]["boot"]["order"],
        fixture_boot_order,
    )
    check(
        "maintenance.boot.up survives from the default beside the overridden sibling key",
        result["infrastructure"]["maintenance"]["boot"]["up"],
        homelabinfra_defaults["infrastructure"]["maintenance"]["boot"]["up"],
    )
    overridden = merged(
        pre_existing_config={"infrastructure": {"domain": "override.example.test"}}
    )
    check(
        "a higher layer overrides one key",
        overridden["infrastructure"]["domain"],
        "override.example.test",
    )
    check(
        "a higher layer overriding one key keeps its sibling",
        overridden["infrastructure"]["reverse_proxy"]["provider"],
        config_infrastructure["reverse_proxy"]["provider"],
    )
    PROVIDER_CASES = [
        ("caddy.yml", "reverse_proxy", "caddy"),
        ("nginx.yml", "reverse_proxy", "nginx"),
        ("authentik.yml", "sso", "authentik"),
        ("pihole.yml", "dns", "pihole"),
        ("opnsense.yml", "dns", "opnsense"),
        ("uptime-kuma.yml", "monitoring", "uptime_kuma"),
    ]

    def render_when(expr, homelabinfra_infra, extra=None):
        ctx = {"homelabinfra_infra": homelabinfra_infra, **(extra or {})}
        return render("{{ (%s) }}" % expr, **ctx) is True

    def gate_condition(task):
        when = task.get("when")
        if when is None:
            return None
        if isinstance(when, list):
            return " and ".join(("(%s)" % w for w in when))
        return str(when)

    for filename, role_key, provider in PROVIDER_CASES:
        path = repo / "ansible" / "tasks" / "wiring" / filename
        wiring_tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
        gated = [t for t in wiring_tasks if t.get("when") is not None]
        if not gated:
            raise AssertionError(
                "%s: no gated task found — cannot confirm the no-op contract" % filename
            )
        task = gated[0]
        expr = gate_condition(task)
        try:
            ran = render_when(expr, {})
        except Exception as exc:
            raise AssertionError(
                "%s: could not render its when: condition (%s)" % (filename, exc)
            )
        check(
            "%s is a no-op when homelabinfra_infra has no %r key"
            % (filename, role_key),
            ran,
            False,
        )
        ran = render_when(expr, {role_key: {"provider": "none"}})
        check(
            "%s is a no-op when %s.provider is none" % (filename, role_key), ran, False
        )
        ran = render_when(expr, {role_key: {"provider": "not-" + provider}})
        check(
            "%s does not fire for a different provider on %s" % (filename, role_key),
            ran,
            False,
        )
        ran = render_when(expr, {role_key: {"provider": provider}})
        check(
            "%s fires when %s.provider is %r" % (filename, role_key, provider),
            ran,
            True,
        )

    def find_task(block_tasks, name):
        for task in block_tasks:
            if task.get("name") == name:
                return task
            nested = task.get("block")
            if nested:
                found = find_task(nested, name)
                if found is not None:
                    return found
        return None

    def assert_holds(task, ctx):
        """True if every condition in an assert task's `that:` currently evaluates true."""
        that = task["ansible.builtin.assert"]["that"]
        if isinstance(that, str):
            that = [that]
        return all(
            (
                render_when(cond, ctx.get("homelabinfra_infra", {}), extra=ctx)
                for cond in that
            )
        )

    CREDENTIAL_CASES = [
        (
            "nginx.yml",
            "Wire Nginx | Assert NPM credentials are available",
            {"reverse_proxy": {"provider": "nginx"}},
            {
                "reverse_proxy": {
                    "provider": "nginx",
                    "admin_user": "admin",
                    "admin_password": "s3cret-test",
                }
            },
            {},
        ),
        (
            "authentik.yml",
            "Wire Authentik | Assert wiring contract vars",
            {"sso": {"provider": "authentik"}},
            {
                "sso": {
                    "provider": "authentik",
                    "host": "http://authentik.example.test",
                    "token": "test-token",
                }
            },
            {
                "_ak_mode": "forward_auth",
                "wiring_app_name": "sample-app",
                "wiring_domain": "sample-app.lab.example.test",
                "wiring_upstream_host": "198.51.100.50",
                "wiring_upstream_port": 8080,
            },
        ),
        (
            "pihole.yml",
            "Wire Pihole | Assert wiring contract vars",
            {"dns": {"provider": "pihole"}},
            {"dns": {"provider": "pihole", "api_key": "test-app-password"}},
            {"wiring_domain": "sample-app.lab.example.test", "_ph_ip": "198.51.100.5"},
        ),
        (
            "opnsense.yml",
            "Wire OPNsense | Assert wiring contract vars",
            {"dns": {"provider": "opnsense"}},
            {
                "dns": {
                    "provider": "opnsense",
                    "api_key": "test-key",
                    "api_secret": "test-secret",
                }
            },
            {
                "wiring_domain": "sample-app.lab.example.test",
                "_opn_hostname": "sample-app",
                "_opn_zone": "lab.example.test",
                "_opn_ip": "198.51.100.5",
            },
        ),
    ]
    for (
        filename,
        task_name,
        infra_no_creds,
        infra_with_creds,
        extra_vars,
    ) in CREDENTIAL_CASES:
        path = repo / "ansible" / "tasks" / "wiring" / filename
        wiring_tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
        gated = [t for t in wiring_tasks if t.get("when") is not None]
        if not gated or "block" not in gated[0]:
            raise AssertionError(
                "%s: no gated block found — cannot locate %r" % (filename, task_name)
            )
        task = find_task(gated[0]["block"], task_name)
        if task is None:
            raise AssertionError(
                "%s: guarding task %r not found -- the credential check moved or was removed; update this test to where it now lives"
                % (filename, task_name)
            )
        ctx_no_creds = {"homelabinfra_infra": infra_no_creds, **extra_vars}
        ctx_with_creds = {"homelabinfra_infra": infra_with_creds, **extra_vars}
        try:
            without = assert_holds(task, ctx_no_creds)
            withc = assert_holds(task, ctx_with_creds)
        except Exception as exc:
            raise AssertionError(
                "%s: could not evaluate %r (%s)" % (filename, task_name, exc)
            )
        check(
            "%s: %r fails (degrades) when the configured provider has no credentials"
            % (filename, task_name),
            without,
            False,
        )
        check(
            "%s: %r succeeds once the configured provider has credentials"
            % (filename, task_name),
            withc,
            True,
        )
    kuma_tasks = yaml.safe_load(
        (repo / "ansible" / "tasks" / "wiring" / "uptime-kuma.yml").read_text(
            encoding="utf-8"
        )
    )
    kuma_gated = [t for t in kuma_tasks if t.get("when") is not None]
    kuma_task = (
        find_task(
            kuma_gated[0]["block"],
            "Wire Uptime Kuma | Record what the registry supplies",
        )
        if kuma_gated
        else None
    )
    if kuma_task is None:
        raise AssertionError(
            "uptime-kuma.yml: credential-recording task not found -- update this test to where it now lives"
        )
    else:
        kuma_expr = kuma_task["ansible.builtin.set_fact"]["_kuma_credentialed"]
        no_creds = render(
            kuma_expr, homelabinfra_infra={"monitoring": {"provider": "uptime_kuma"}}
        )
        with_creds = render(
            kuma_expr,
            homelabinfra_infra={
                "monitoring": {
                    "provider": "uptime_kuma",
                    "host": "http://kuma.example.test",
                    "admin_user": "admin",
                    "admin_password": "test-password",
                }
            },
        )
        check(
            "uptime-kuma.yml: _kuma_credentialed is false without registry credentials",
            str(no_creds).strip().lower() == "true",
            False,
        )
        check(
            "uptime-kuma.yml: _kuma_credentialed is true with registry credentials",
            str(with_creds).strip().lower() == "true",
            True,
        )


def network_selection():
    task_file = repo / "ansible" / "tasks" / "network" / "resolve-network.yml"
    tasks = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    SET_FACT = ("ansible.builtin.set_fact", "set_fact")

    def set_fact_of(task):
        for key in SET_FACT:
            if key in task:
                return task[key]
        return {}

    decide = next((t for t in tasks if "network_selected" in set_fact_of(t)), None)
    if decide is None:
        raise SystemExit(
            "network-scope test: no set_fact publishing network_selected in resolve-network.yml. The decision moved; update this test to read it where it now lives."
        )
    ORDER = [
        "_entry",
        "_domains",
        "_default_estate",
        "_estate",
        "_estate_network",
        "_shared",
        "_requested",
        "_hint",
    ]

    def resolve(catalog, config, app="", estate="", shared=None, requested=""):
        ctx = {
            "_resolve_network_catalog": catalog,
            "homelabinfra_config": config,
            "resolve_network_app": app,
            "resolve_network_estate": estate,
            "resolve_network_requested": requested,
        }
        if shared is not None:
            ctx["resolve_network_shared"] = shared
        declared = decide["vars"]
        for name in ORDER:
            if name in declared:
                ctx[name] = render(str(declared[name]), **ctx)
        return {
            key: render(str(expr), **ctx) for key, expr in set_fact_of(decide).items()
        }

    catalog = yaml.safe_load(
        (repo / "catalog/applications.yml").read_text(encoding="utf-8")
    )
    FLAT = {"networks": {"default": {}}, "infrastructure": {}}
    SEGMENTED = {
        "networks": {
            "default": {},
            "shared": {},
            "personal": {},
            "foxglove": {},
            "iot": {},
        },
        "infrastructure": {
            "domains": {
                "personal": {"domain": "a.example", "default": True},
                "foxglove": {"domain": "b.example"},
            }
        },
    }
    NAMED_ESTATE_NET = {
        "networks": {"default": {}, "shared": {}, "vlan21": {}},
        "infrastructure": {
            "domains": {
                "personal": {"domain": "a.example", "default": True},
                "foxglove": {"domain": "b.example", "network": "vlan21"},
            }
        },
    }
    check(
        "flat lab, lab-scoped app",
        resolve(catalog, FLAT, app="caddy")["network_selected"],
        "default",
    )
    check(
        "flat lab, estate app",
        resolve(catalog, FLAT, app="sonarr")["network_selected"],
        "default",
    )
    check(
        "flat lab, shared stack",
        resolve(catalog, FLAT, shared=True)["network_selected"],
        "default",
    )
    check(
        "flat lab, no domains map, no app",
        resolve(catalog, FLAT)["network_selected"],
        "default",
    )
    check(
        "lab-scoped app takes shared",
        resolve(catalog, SEGMENTED, app="caddy")["network_selected"],
        "shared",
    )
    check(
        "the vault takes shared",
        resolve(catalog, SEGMENTED, app="vaultwarden")["network_selected"],
        "shared",
    )
    check(
        "estate app, named estate",
        resolve(catalog, SEGMENTED, app="sonarr", estate="foxglove")[
            "network_selected"
        ],
        "foxglove",
    )
    check(
        "estate app, unnamed estate takes the DEFAULT estate, not shared",
        resolve(catalog, SEGMENTED, app="sonarr")["network_selected"],
        "personal",
    )
    check(
        "an estate's own SSO stays in its estate",
        resolve(catalog, SEGMENTED, app="authentik", estate="foxglove")[
            "network_selected"
        ],
        "foxglove",
    )
    check(
        "shared stack takes shared",
        resolve(catalog, SEGMENTED, estate="foxglove", shared=True)["network_selected"],
        "shared",
    )
    check(
        "ordinary stack stays in its estate",
        resolve(catalog, SEGMENTED, estate="foxglove", shared=False)[
            "network_selected"
        ],
        "foxglove",
    )
    check(
        "shared passed as an Ansible string, not a bool",
        resolve(catalog, SEGMENTED, estate="foxglove", shared="True")[
            "network_selected"
        ],
        "shared",
    )
    check(
        "explicit network beats scope",
        resolve(catalog, SEGMENTED, app="caddy", requested="iot")["network_selected"],
        "iot",
    )
    check(
        "explicit network beats an estate",
        resolve(catalog, SEGMENTED, app="sonarr", estate="foxglove", requested="iot")[
            "network_selected"
        ],
        "iot",
    )
    check(
        "estate names its own network",
        resolve(catalog, NAMED_ESTATE_NET, app="sonarr", estate="foxglove")[
            "network_selected"
        ],
        "vlan21",
    )
    check(
        "an estate declaring no network, and none named after it, falls back",
        resolve(catalog, NAMED_ESTATE_NET, app="sonarr", estate="personal")[
            "network_selected"
        ],
        "default",
    )
    check(
        "hint names the estate even in a flat lab",
        resolve(catalog, FLAT, app="sonarr", estate="foxglove")["network_hint"],
        "foxglove",
    )
    check(
        "hint names shared even in a flat lab",
        resolve(catalog, FLAT, app="caddy")["network_hint"],
        "shared",
    )
    effective = next((t for t in tasks if "network_effective" in set_fact_of(t)), None)
    if effective is None:
        raise AssertionError(
            "resolve-network.yml publishes no network_effective; callers would have to re-merge networks.default themselves, which is the bug this guards"
        )
    else:
        BANDS = {
            "networks": {
                "default": {"cidr": "198.51.100.0/24", "bridge": "vmbr1", "vlan": 0},
                "shared": {"ip_offset": 1034, "max_hosts": 32},
                "personal": {"ip_offset": 1066},
            },
            "infrastructure": {
                "domains": {"personal": {"domain": "a.example", "default": True}}
            },
        }

        def effective_for(config, **kwargs):
            selected = resolve(catalog, config, **kwargs)["network_selected"]
            expression = str(set_fact_of(effective)["network_effective"])
            return render(
                expression, homelabinfra_config=config, network_selected=selected
            )

        shared_band = effective_for(BANDS, app="caddy")
        check("a band inherits the subnet", shared_band.get("cidr"), "198.51.100.0/24")
        check("a band inherits the bridge", shared_band.get("bridge"), "vmbr1")
        check("a band keeps its own offset", shared_band.get("ip_offset"), 1034)
        estate_band = effective_for(BANDS, app="sonarr")
        check(
            "the estate band inherits the same subnet",
            estate_band.get("cidr"),
            "198.51.100.0/24",
        )
        check(
            "the estate band keeps its own offset", estate_band.get("ip_offset"), 1066
        )
        check(
            "a band does not inherit its neighbour's cap",
            estate_band.get("max_hosts"),
            None,
        )


def doctor_and_secret_shape():
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "PROXMOX_API_TOKEN",
            "PROXMOX_API_TOKEN_ID",
            "PROXMOX_API_USER",
            "VAULTWARDEN_ADMIN_TOKEN",
            "CLOUDFLARE_API_TOKEN",
        }
    }

    def doctor(path):
        return subprocess.run(
            ["bash", str(repo / "ansible/scripts/config-doctor.sh"), str(path)],
            capture_output=True,
            text=True,
            env=env,
        )

    for name in ("valid", "multi-estate"):
        result = doctor(fixtures / name)
        assert result.returncode == 0, result.stdout + result.stderr
    invalid = doctor(fixtures / "invalid")
    assert invalid.returncode != 0, "invalid configuration accepted"
    output = invalid.stdout + invalid.stderr
    for filename, key in (
        ("proxmox.yml", "proxmox.node"),
        ("proxmox.yml", "networks.default.gateway"),
        ("apps/broken-app.yml", "routing"),
        ("apps/broken-app.yml", "stack"),
    ):
        assert any(
            filename in line and key in line for line in output.splitlines()
        ), output
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        (target / "proxmox.yml").write_text(
            (fixtures / "multi-estate/proxmox.yml").read_text()
        )
        infra = yaml.safe_load(
            (fixtures / "multi-estate/infrastructure.yml").read_text()
        )
        for estate in infra["domains"].values():
            estate.pop("default", None)
        (target / "infrastructure.yml").write_text(yaml.safe_dump(infra))
        result = doctor(target)
        assert (
            result.returncode != 0
        ), "multi-estate config accepted without an explicit default"
        assert (
            "multi-estate map must declare exactly one default: true"
            in result.stdout + result.stderr
        )
    for document, code in (
        (
            {
                "domain": "fixture.invalid",
                "reverse_proxy": {"provider": "caddy", "host": "http://198.51.100.5"},
            },
            0,
        ),
        ({"reverse_proxy": {"admin_password": "fixture-secret"}}, 2),
        ({"backups": {"api_token_id": "fixture-id"}}, 0),
    ):
        result = subprocess.run(
            [sys.executable, str(repo / "ansible/scripts/secret-shape.py")],
            input=json.dumps(document),
            capture_output=True,
            text=True,
        )
        check("generated fact secret shape", result.returncode, code)
        if code:
            assert "admin_password" in result.stderr, result.stderr


if __name__ == "__main__":
    doctor_and_secret_shape()
    fixture_layers_and_provider_gates()
    configuration_layers_and_estates()
    network_selection()
    print("Config: doctor, precedence, estates, mail, providers and networks passed")
