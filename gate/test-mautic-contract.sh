#!/usr/bin/env bash
# Focused static contract checks for issue #56 — Mautic.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
need() { grep -Fq -- "$2" "$1" || { echo "missing Mautic contract: $2" >&2; exit 1; }; }

need "$repo/ansible/vars/app-defaults/mautic.yml" 'stack: services'
need "$repo/ansible/vars/app-defaults/mautic.yml" 'provider: mariadb'
need "$repo/ansible/vars/app-defaults/mautic.yml" 'instance: mariadb-mautic'
need "$repo/ansible/roles/mautic/tasks/main.yml" 'vault_item_name: "homelab-infra/apps/{{ instance }}"'
need "$repo/ansible/roles/mautic/tasks/main.yml" 'vault_item_secret_fields: [admin_password, database_password]'
need "$repo/ansible/roles/mautic/tasks/main.yml" 'include_tasks: ../../../tasks/mail/resolve-mail.yml'
need "$repo/ansible/roles/mautic/templates/local.php.j2" "wiring_mail.enabled"
need "$repo/ansible/roles/mautic/templates/local.php.j2" "'mailer_dsn'"
need "$repo/ansible/playbooks/apps/mautic.yml" 'tasks/database/provision.yml'
need "$repo/ansible/playbooks/apps/mautic.yml" 'stack/find-or-create-host.yml'

# The image's own entrypoint (mautic/docker-mautic:common/docker-entrypoint.sh) refuses
# to start without MAUTIC_DB_HOST/MAUTIC_DB_USER/MAUTIC_DB_PASSWORD and a valid
# DOCKER_MAUTIC_ROLE — and never runs scheduled campaigns/segments without a mautic_cron
# service, since this image does not read a MAUTIC_RUN_CRON_JOBS variable.
need "$repo/ansible/roles/mautic/templates/docker-compose.yml.j2" 'MAUTIC_DB_HOST: "{{ app_config.app.database_host }}"'
need "$repo/ansible/roles/mautic/templates/docker-compose.yml.j2" 'MAUTIC_DB_USER: "{{ app_config.app.database_user }}"'
need "$repo/ansible/roles/mautic/templates/docker-compose.yml.j2" 'MAUTIC_DB_PASSWORD: "{{ app_config.app.database_password }}"'
need "$repo/ansible/roles/mautic/templates/docker-compose.yml.j2" 'mautic_cron:'
need "$repo/ansible/roles/mautic/templates/docker-compose.yml.j2" 'mautic_worker:'
need "$repo/ansible/roles/mautic/templates/docker-compose.yml.j2" 'DOCKER_MAUTIC_ROLE: mautic_cron'
need "$repo/ansible/roles/mautic/templates/docker-compose.yml.j2" 'DOCKER_MAUTIC_ROLE: mautic_worker'

# Confirmed against mautic/docker-mautic's own common/startup/wait_for_mautic_install.sh
# and common/entrypoint_mautic_web.sh: both treat non-empty db_driver + site_url in
# local.php as their only "already installed" signal, so the seed this role writes
# before mautic:install has ever run must not carry either key.
python3 - "$repo/ansible/roles/mautic/templates/local.php.j2" <<'PYEOF'
import sys
content = open(sys.argv[1]).read()
assert "'db_driver'" not in content, "the seed template must not write db_driver"
assert "'site_url'" not in content, "the seed template must not write site_url"
assert "replace('%', '%%')" in content, "seed values must have literal '%' doubled for Symfony's DI parameter bag too"
assert "replace('\\\\', '\\\\\\\\')" in content, "seed values must be PHP-escaped too"
print("Mautic local.php seed correctly omits the installed-state sentinel: OK")
PYEOF

# The rendered Compose file embeds MAUTIC_DB_PASSWORD; a diff/failure of the render task
# must not print it. The installer runs as www-data, the same user every persisted
# volume is chowned to, so its generated cache/config files stay usable by the web,
# cron and worker services. local.php is seeded only when absent — never re-templated
# wholesale — and then converged field-by-field, so Mautic's own secret_key (and
# anything else this role does not generate) survives a second deploy. db_driver and
# site_url are converged only after `.installed` exists, and every converged value is
# PHP-escaped and inserted inside the array rather than appended after its closing `);`.
python3 - "$repo/ansible/roles/mautic/tasks/main.yml" <<'PYEOF'
import sys, yaml
tasks = yaml.safe_load(open(sys.argv[1]))
by_name = {t.get("name"): t for t in tasks}

render = by_name["Mautic | Render Compose project"]
assert render.get("no_log") is True, "Render Compose project task must set no_log: true"

assert "Mautic | Render configuration" not in by_name, \
    "local.php must not be unconditionally re-templated every run"

seed = by_name["Mautic | Seed configuration on first deploy"]
assert seed.get("when") == "not _mautic_local_php_stat.stat.exists", \
    "the seed task must only run when local.php does not already exist"
assert seed.get("no_log") is True, "the seed task embeds MAUTIC_DB_PASSWORD and must be no_log"

converge = by_name["Mautic | Converge platform-owned configuration fields"]
assert converge.get("no_log") is True, "the converge task embeds MAUTIC_DB_PASSWORD and must be no_log"
lineinfile = converge["ansible.builtin.lineinfile"]
assert lineinfile["path"] == "{{ mautic_config_file }}"
assert lineinfile.get("insertbefore"), \
    "a key absent from local.php must be inserted before the closing ');', not appended after it"
line_tmpl = lineinfile["line"]
assert "replace('%', '%%')" in line_tmpl, \
    "every value written into local.php must have its literal '%' doubled for Symfony's DI parameter bag"
assert "replace('\\\\', '\\\\\\\\')" in line_tmpl and "replace(\"'\", \"\\\\'\")" in line_tmpl, \
    "every value written into local.php must be escaped for a PHP single-quoted string"
fields_expr = converge["vars"]["_mautic_local_php_fields"]
assert "_mautic_installed_marker.stat.exists else []" in fields_expr, \
    "db_driver/site_url must stay out of local.php until installation has completed"
assert fields_expr.index("db_driver") < fields_expr.index("_mautic_installed_marker.stat.exists else []"), \
    "db_driver must be inside the installed-only branch of the fields list"

# Actually render the four (installed x mail-enabled) states through Jinja2, rather
# than grep the source, to prove the converged key is the single `mailer_dsn` the
# pinned mautic/mautic 6.0.6 tag actually reads (app/bundles/EmailBundle/Config/
# config.php's `parameters` array — the seven mailer_transport/host/port/user/
# password/encryption/auth_mode keys this role wrote before appear nowhere in that
# file or in EmailBundle's ConfigType form) — and that disabling mail after it was
# configured replaces the credentialed DSN with Mautic's own shipped no-relay
# default instead of the lineinfile loop simply going empty and leaving the old
# relay/credentials live.
# NativeEnvironment, not plain Environment: Ansible's own templar returns the real
# Python object for a template that is a single whole expression (which is exactly
# what makes `loop: "{{ _mautic_local_php_fields }}"` iterate dicts rather than parse
# a string) — plain Jinja2 would only hand back that object's str() here.
from jinja2.nativetypes import NativeEnvironment
import urllib.parse
class AttrDict(dict):
    def __getattr__(self, key):
        return self[key]
def wrap(value):
    return AttrDict({k: wrap(v) for k, v in value.items()}) if isinstance(value, dict) else value

env = NativeEnvironment()
env.filters["bool"] = bool
env.filters["urlencode"] = urllib.parse.quote
tmpl = env.from_string(fields_expr)
line_tmpl = NativeEnvironment().from_string(line_tmpl)
app_config = wrap({"app": {"database_host": "h", "database_port": 3306, "database": {"name": "mautic"},
                            "database_user": "u", "database_password": "p", "name": "Mautic"}})
installed = wrap({"stat": {"exists": True}})

# The pinned symfony/mailer 6.4.13's own Transport/Smtp/EsmtpTransportFactory.php
# supports exactly `smtp` and `smtps`, and only `smtps` selects implicit TLS —
# `mail.encryption`'s three real values (CONTRACT.md, config-doctor.sh:
# starttls | tls | none — never `ssl`) must map onto that pair explicitly, not through
# a two-way branch that tests the impossible `ssl` value.
expected_scheme = {"starttls": "smtp", "tls": "smtps", "none": "smtp"}
for encryption, scheme in expected_scheme.items():
    wiring_mail_enabled = wrap({"enabled": True, "from_name": "X", "from_address": "a@b.com",
                                "host": "smtp.example.com", "port": 587,
                                "username": "user@example.com", "password": "p@ss:w/ord'\\%x",
                                "encryption": encryption})
    fields_on = tmpl.render(app_config=app_config, wiring_mail=wiring_mail_enabled,
                             mautic_fqdn="m.example.com", _mautic_installed_marker=installed)
    by_key_on = {f["key"]: f["value"] for f in fields_on}
    assert "mailer_transport" not in by_key_on, \
        "mailer_transport is not a Mautic 6.0.6 config key; the pinned release only reads mailer_dsn"

    dsn_field = next(f for f in fields_on if f["key"] == "mailer_dsn")
    raw_dsn = dsn_field["value"]
    parsed = urllib.parse.urlsplit(raw_dsn)
    assert parsed.scheme == scheme, f"encryption={encryption!r} must select scheme {scheme!r}, got {parsed.scheme!r}"
    assert urllib.parse.unquote(parsed.username) == "user@example.com"
    assert urllib.parse.unquote(parsed.password) == "p@ss:w/ord'\\%x", \
        "the runtime DSN must round-trip a password containing '@', ':', '/', an apostrophe, a backslash and a percent"
    assert parsed.hostname == "smtp.example.com" and parsed.port == 587

    # The ON-DISK value is a second, distinct thing from the runtime DSN above: every
    # literal '%' in the urlencoded DSN must be doubled (Symfony DI parameter escaping,
    # confirmed against app/bundles/ConfigBundle/Form/Type/EscapeTransformer.php), or
    # the container fails to compile with a missing-parameter error. Simulate exactly
    # that unescape (Symfony's ParameterBag does the same '%%' -> '%' substitution when
    # it resolves this parameter) and confirm it reproduces the runtime DSN.
    on_disk_line = line_tmpl.render(item=dsn_field)
    on_disk_value = on_disk_line.split("=> '", 1)[1].rsplit("',", 1)[0]
    assert on_disk_value.count("%%") >= raw_dsn.count("%"), \
        "every literal '%' in the runtime DSN must be doubled in the on-disk local.php value"
    assert on_disk_value.replace("%%", "%") == raw_dsn, \
        "unescaping the on-disk value's doubled percents must reproduce the runtime DSN exactly"

wiring_mail_disabled = wrap({"enabled": False, "from_name": "", "from_address": "", "host": "",
                             "port": "", "username": "", "password": "", "encryption": ""})
fields_off = tmpl.render(app_config=app_config, wiring_mail=wiring_mail_disabled,
                          mautic_fqdn="m.example.com", _mautic_installed_marker=installed)
by_key_off = {f["key"]: f["value"] for f in fields_off}
assert by_key_off["mailer_dsn"] == "smtp://localhost:25", \
    "disabling mail must reset mailer_dsn to Mautic's own shipped no-relay default, not leave the old DSN"
print("Mautic converges mailer_dsn with the correct scheme per encryption value, round-tripping a hostile "
      "password through both the on-disk (percent-doubled) and runtime forms: OK")

# config-doctor.sh explicitly accepts mail.encryption: "" as valid authored config,
# and tasks/mail/resolve-mail.yml's bare `default('starttls')` (no boolean mode) does
# NOT catch that blank — only an undefined key. Render the normalize task's own
# expression with encryption="" and confirm it produces 'starttls', the value the
# scheme-mapping dict above can actually index.
normalize = by_name["Mautic | Normalize a blank mail.encryption to its documented default"]
assert normalize.get("no_log") is True, "the normalize task re-assigns the whole wiring_mail dict and must be no_log"
normalize_expr = normalize["ansible.builtin.set_fact"]["wiring_mail"]
norm_env = NativeEnvironment()
norm_env.filters["combine"] = lambda base, other: wrap({**base, **other})
norm_tmpl = norm_env.from_string(normalize_expr)
blank_wiring_mail = wrap({"encryption": "", "host": "h", "port": 25})
normalized = norm_tmpl.render(wiring_mail=blank_wiring_mail)
assert normalized["encryption"] == "starttls", \
    "a blank mail.encryption must normalize to starttls, not survive as '' into the scheme-mapping dict lookup"

# mail.encryption: starttls is Symfony Mailer's weakest guarantee — smtp:// only
# ATTEMPTS StartTLS if the server offers it in its EHLO response and has no DSN option
# to require and abort otherwise (confirmed against the pinned symfony/mailer 6.4.13's
# Transport/Smtp/EsmtpTransport.php). A relay that stops advertising STARTTLS must
# fail this deploy rather than silently send the relay password in the clear.
preflight = by_name["Mautic | Check whether the mail relay actually advertises STARTTLS"]
assert preflight["when"] == ["wiring_mail.enabled | default(false) | bool", "wiring_mail.encryption == 'starttls'"]
assert "has_extn(\"starttls\")" in preflight["ansible.builtin.command"]["argv"][2]
refuse = by_name["Mautic | Refuse to deploy when the relay cannot actually enforce STARTTLS"]
assert refuse["when"] == [
    "wiring_mail.enabled | default(false) | bool",
    "wiring_mail.encryption == 'starttls'",
    "_mautic_starttls_check.rc | default(1) != 0",
]
print("Mautic normalizes a blank mail.encryption and preflights STARTTLS support before deploying: OK")

installer = by_name["Mautic | Run the non-interactive installer"]
argv = installer["ansible.builtin.command"]["argv"]
assert "--user" in argv and argv[argv.index("--user") + 1] == "www-data", \
    "installer must run as --user www-data"
print("Mautic secret-handling, local.php-preservation and installer-user checks: OK")
PYEOF

need "$repo/catalog/applications.yml" 'job: deploy-mautic.yaml'
need "$repo/rundeck/jobs/deploy-mautic.yaml" 'Run playbooks/apps/mautic.yml'

echo "PASS: Mautic named MariaDB backend, mail contract, secrets, and surface"
