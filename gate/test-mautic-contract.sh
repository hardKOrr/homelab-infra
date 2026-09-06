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
need "$repo/ansible/roles/mautic/templates/local.php.j2" "'mailer_transport' => 'smtp'"
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
assert "replace('\\\\', '\\\\\\\\')" in line_tmpl and "replace(\"'\", \"\\\\'\")" in line_tmpl, \
    "every value written into local.php must be escaped for a PHP single-quoted string"
fields_expr = converge["vars"]["_mautic_local_php_fields"]
assert "_mautic_installed_marker.stat.exists else []" in fields_expr, \
    "db_driver/site_url must stay out of local.php until installation has completed"
assert fields_expr.index("db_driver") < fields_expr.index("_mautic_installed_marker.stat.exists else []"), \
    "db_driver must be inside the installed-only branch of the fields list"

# Actually render the four (installed x mail-enabled) states through Jinja2, rather
# than grep the source, to prove disabling mail after it was configured clears the
# relay/credentials instead of the lineinfile loop simply going empty.
# NativeEnvironment, not plain Environment: Ansible's own templar returns the real
# Python object for a template that is a single whole expression (which is exactly
# what makes `loop: "{{ _mautic_local_php_fields }}"` iterate dicts rather than parse
# a string) — plain Jinja2 would only hand back that object's str() here.
from jinja2.nativetypes import NativeEnvironment
class AttrDict(dict):
    def __getattr__(self, key):
        return self[key]
def wrap(value):
    return AttrDict({k: wrap(v) for k, v in value.items()}) if isinstance(value, dict) else value

env = NativeEnvironment()
env.filters["bool"] = bool
tmpl = env.from_string(fields_expr)
app_config = wrap({"app": {"database_host": "h", "database_port": 3306, "database": {"name": "mautic"},
                            "database_user": "u", "database_password": "p", "name": "Mautic"}})
wiring_mail_disabled = wrap({"enabled": False, "from_name": "", "from_address": "", "host": "",
                             "port": "", "username": "", "password": "", "encryption": ""})
installed = wrap({"stat": {"exists": True}})
fields = tmpl.render(app_config=app_config, wiring_mail=wiring_mail_disabled,
                      mautic_fqdn="m.example.com", _mautic_installed_marker=installed)
by_key = {f["key"]: f["value"] for f in fields}
assert by_key["mailer_transport"] == "mail", \
    "disabling mail must reset mailer_transport off the stale smtp relay"
for key in ("mailer_host", "mailer_user", "mailer_password", "mailer_encryption", "mailer_auth_mode"):
    assert by_key[key] == "", f"disabling mail must clear {key}, not leave the prior value in local.php"
print("Mautic mail-disable convergence actually clears the relay/credentials: OK")

installer = by_name["Mautic | Run the non-interactive installer"]
argv = installer["ansible.builtin.command"]["argv"]
assert "--user" in argv and argv[argv.index("--user") + 1] == "www-data", \
    "installer must run as --user www-data"
print("Mautic secret-handling, local.php-preservation and installer-user checks: OK")
PYEOF

need "$repo/catalog/applications.yml" 'job: deploy-mautic.yaml'
need "$repo/rundeck/jobs/deploy-mautic.yaml" 'Run playbooks/apps/mautic.yml'

echo "PASS: Mautic named MariaDB backend, mail contract, secrets, and surface"
