#!/usr/bin/env bash
# Focused regression tests for the platform outbound-mail contract — issue #47.
#
# 1. config-doctor.sh accepts a complete generic-SMTP block, rejects an incomplete one,
#    rejects a bad encryption enum, and rejects a secret-bearing (password/api_key/
#    api_secret/token) mail block outright — tracked config must never carry the
#    credential (ansible/vars/CONTRACT.md, "mail — the platform outbound-mail contract").
# 2. vault-runtime.py maps a canonical homelab-infra/mail item into the in-memory
#    contract the same way every other role key is mapped.
# 3. secret-shape.py still rejects a generated-facts fixture that carries a mail
#    password, proving the existing generic redaction covers the new role key without
#    a code change.
# 4. The registry-overlay and per-app injection seams exist and reference the
#    documented contract (load-user-vars.yml, vaultwarden-cutover.yml, resolve-mail.yml).
# 5. A representative SMTP consumer renders an estate's selected From identity.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
work="$(mktemp -d "${TMPDIR:-/tmp}/homelab-mail-test.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT

fail() { echo "mail-contract test failed: $*" >&2; exit 1; }

python3 "$repo/ansible/scripts/resolve-python.sh" >/dev/null 2>&1 || true

# ── 1. config-doctor.sh ────────────────────────────────────────────────────────
base="$repo/gate/fixtures/config/valid"
cp -r "$base" "$work/base"
infra="$work/base/infrastructure.yml"

replace_mail_block() {
  # $1: replacement `mail:` block (including trailing newline)
  python3 - "$infra" "$1" <<'PY'
import re, sys
path, block = sys.argv[1], sys.argv[2]
text = open(path).read()
text = re.sub(r"mail:\n(?:  .*\n)*", block, text, count=1)
open(path, "w").write(text)
PY
}

doctor() {
  bash "$repo/ansible/scripts/config-doctor.sh" "$work/base" 2>&1
}

# Absent mail: block entirely (every checkout that predates this contract) must still
# pass -- mail.provider is optional and defaults to disabled, exactly like the explicit
# `none` the tracked fixtures declare. Exercised in a separate copy, not $infra itself,
# so the regex-based mutations below still find a `mail:` anchor to replace.
nomail_dir="$work/nomail"
cp -r "$base" "$nomail_dir"
python3 - "$nomail_dir/infrastructure.yml" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path).read()
text = re.sub(r"mail:\n(?:  .*\n)*", "", text, count=1)
open(path, "w").write(text)
PY
out="$(bash "$repo/ansible/scripts/config-doctor.sh" "$nomail_dir" 2>&1)" \
  || fail "an infrastructure.yml with no mail: block must still pass config-doctor.sh: $out"
grep -q '^OK\|0 error(s)' <<<"$out" || fail "absent mail block reported an error: $out"

# Complete generic-SMTP block must pass.
replace_mail_block $'mail:\n  provider: smtp\n  host: "smtp.example.test"\n  port: 587\n  from_address: "lab@example.test"\n'
out="$(doctor)" || fail "complete smtp mail block must pass config-doctor.sh: $out"
grep -q '^OK\|0 error(s)' <<<"$out" || fail "complete smtp mail block reported an error: $out"

# An estate identity may omit shared relay settings, but must provide its own From address.
estate_dir="$work/estate"
cp -r "$base" "$estate_dir"
# The base fixture has an unscoped application filename; remove it so this temporary
# copy can exercise the domains map without also testing estate instance naming.
rm -rf "$estate_dir/apps"
mkdir -p "$estate_dir/apps"
python3 - "$estate_dir/infrastructure.yml" <<'PYESTATE'
import sys
import yaml
path = sys.argv[1]
data = yaml.safe_load(open(path))
data["domains"] = {
    "personal": {"domain": "lab.example.test", "default": True},
    "foxglove": {
        "domain": "foxglove.example.test",
        "mail": {"from_address": "hello@foxglove.example.test"},
    },
}
with open(path, "w") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PYESTATE
out="$(bash "$repo/ansible/scripts/config-doctor.sh" "$estate_dir" 2>&1)" \
  || fail "valid estate mail overlay must pass config-doctor.sh: $out"
grep -q '^OK\|0 error(s)' <<<"$out" || fail "estate mail overlay reported an error: $out"

# An active inherited SMTP relay still requires an estate-authored From address. This
# exercises the require_identity guard rather than the global provider-none short-circuit.
missing_identity_dir="$work/estate-missing-identity"
cp -r "$base" "$missing_identity_dir"
rm -rf "$missing_identity_dir/apps"
mkdir -p "$missing_identity_dir/apps"
python3 - "$missing_identity_dir/infrastructure.yml" <<'PYESTATEIDENTITY'
import sys
import yaml
path = sys.argv[1]
data = yaml.safe_load(open(path))
data["domains"] = {
    "personal": {"domain": "lab.example.test", "default": True},
    "foxglove": {
        "domain": "foxglove.example.test",
        "mail": {"from_name": "Foxglove"},
    },
}
data["mail"] = {
    "provider": "smtp",
    "host": "smtp.example.test",
    "port": 587,
    "from_address": "default@lab.example.test",
}
with open(path, "w") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PYESTATEIDENTITY
set +e
out="$(bash "$repo/ansible/scripts/config-doctor.sh" "$missing_identity_dir" 2>&1)"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "estate mail identity must fail when only the inherited relay is active"
grep -qF 'domains.foxglove.mail.from_address' <<<"$out" \
  || fail "missing estate mail identity was not named by config-doctor.sh: $out"

python3 - "$estate_dir/infrastructure.yml" <<'PYESTATESECRET'
import sys
import yaml
path = sys.argv[1]
data = yaml.safe_load(open(path))
data["domains"]["foxglove"]["mail"]["password"] = "must-not-be-authored"
with open(path, "w") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PYESTATESECRET
set +e
out="$(bash "$repo/ansible/scripts/config-doctor.sh" "$estate_dir" 2>&1)"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "estate mail password must fail config-doctor.sh"
grep -qF 'domains.foxglove.mail.password' <<<"$out" \
  || fail "estate mail password was not named by config-doctor.sh: $out"

# Incomplete: provider smtp but no host/port/from_address.
replace_mail_block $'mail:\n  provider: smtp\n'
set +e
out="$(doctor)"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "incomplete mail.provider=smtp block must fail config-doctor.sh"
for expected in 'mail.host: required' 'mail.port: required' 'mail.from_address: required'; do
  grep -qF -- "$expected" <<<"$out" || fail "incomplete mail block missing: $expected ($out)"
done

# Bad encryption enum.
replace_mail_block $'mail:\n  provider: smtp\n  host: "smtp.example.test"\n  port: 587\n  from_address: "lab@example.test"\n  encryption: "plaintext"\n'
set +e
out="$(doctor)"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "mail.encryption=plaintext must fail config-doctor.sh"
grep -qF 'mail.encryption' <<<"$out" || fail "bad encryption did not name mail.encryption: $out"

# Secret-bearing tracked config must be rejected outright, even when otherwise complete.
for secret_field in password api_key api_secret token; do
  replace_mail_block $'mail:\n  provider: smtp\n  host: "smtp.example.test"\n  port: 587\n  from_address: "lab@example.test"\n  '"$secret_field"$': "should-not-be-here"\n'
  set +e
  out="$(doctor)"
  rc=$?
  set -e
  [ "$rc" -ne 0 ] || fail "mail.$secret_field in tracked config must fail config-doctor.sh"
  grep -qF "mail.$secret_field" <<<"$out" \
    || fail "secret-bearing mail.$secret_field was not named in config-doctor.sh output: $out"
done

# ── 2. vault-runtime.py maps homelab-infra/mail like any other role key ───────
printf '%s' '[{"name":"homelab-infra/mail","fields":[{"name":"password","value":"relay-secret"}]},{"name":"homelab-infra/estates/foxglove/mail","fields":[{"name":"password","value":"foxglove-secret"}]}]' \
  | python3 "$repo/ansible/scripts/vault-runtime.py" > "$work/runtime.json"
python3 - "$work/runtime.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["mail"]["password"] == "relay-secret", d
assert d["estates"]["foxglove"]["mail"]["password"] == "foxglove-secret", d
PY

# ── 3. secret-shape.py rejects a mail password in generated facts ─────────────
set +e
out="$(printf '{"mail": {"provider": "smtp", "host": "smtp.example.test", "password": "leaked"}}' \
  | python3 "$repo/ansible/scripts/secret-shape.py" 2>&1)"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "secret-shape.py accepted a mail.password field in generated facts"
grep -qF 'mail.password' <<<"$out" || fail "secret-shape.py did not name mail.password: $out"

# The non-secret half must still pass.
printf '{"mail": {"provider": "smtp", "host": "smtp.example.test", "port": 587}}' \
  | python3 "$repo/ansible/scripts/secret-shape.py" \
  || fail "secret-shape.py rejected a non-secret mail block"

# ── 4. Registry overlay and per-app injection seams exist ─────────────────────
grep -Fq "Overlay the authored mail provider onto the registry" \
  "$repo/ansible/tasks/load-user-vars.yml" \
  || fail "load-user-vars.yml does not overlay the authored mail provider"
grep -Fq "homelab-infra/mail" "$repo/ansible/playbooks/maintenance/vaultwarden-cutover.yml" \
  || fail "vaultwarden-cutover.yml does not import homelab-infra/mail"
grep -Fq "wiring_mail" "$repo/ansible/tasks/mail/resolve-mail.yml" \
  || fail "resolve-mail.yml does not set wiring_mail"
grep -Fq "no_log: true" "$repo/ansible/tasks/mail/resolve-mail.yml" \
  || fail "resolve-mail.yml does not redact the resolved credential"

# Render a real consumer template with the effective estate fact. This stays offline and
# synthetic, but proves the value that reaches an application's SMTP configuration rather
# than only proving that the resolver stores it in an intermediate mapping.
python3 - "$repo" <<'PYTEST'
import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

repo = Path(sys.argv[1])
env = Environment(loader=FileSystemLoader(repo / "ansible" / "roles" / "bookstack" / "templates"))
env.filters["comment"] = lambda value: "# " + str(value)
template = env.get_template("bookstack.env.j2")
rendered = template.render(
    ansible_managed="fixture",
    bookstack_fqdn="bookstack.foxglove.fixture.invalid",
    _bookstack_app_key="fixture-key",
    homelabinfra_config={"timezone": "UTC"},
    app_config={"app": {
        "puid": 1000, "pgid": 1000, "database": {"name": "bookstack"},
        "database_host": "db.fixture.invalid", "database_port": 3306,
        "database_user": "bookstack", "database_password": "fixture-db-secret",
    }},
    wiring_mail={
        "enabled": True, "from_address": "hello@foxglove.fixture.invalid",
        "from_name": "Foxglove", "host": "smtp.fixture.invalid", "port": 587,
        "username": "shared-login", "password": "fixture-mail-secret",
        "encryption": "starttls",
    },
)
assert "MAIL_FROM=hello@foxglove.fixture.invalid" in rendered, rendered
assert "MAIL_FROM_NAME=Foxglove" in rendered, rendered
PYTEST

echo "OK: mail contract schema, validation, vault mapping, redaction and wiring seams"
