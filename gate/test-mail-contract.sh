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

# Complete generic-SMTP block must pass.
replace_mail_block $'mail:\n  provider: smtp\n  host: "smtp.example.test"\n  port: 587\n  from_address: "lab@example.test"\n'
out="$(doctor)" || fail "complete smtp mail block must pass config-doctor.sh: $out"
grep -q '^OK\|0 error(s)' <<<"$out" || fail "complete smtp mail block reported an error: $out"

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
printf '%s' '[{"name":"homelab-infra/mail","fields":[{"name":"password","value":"relay-secret"}]}]' \
  | python3 "$repo/ansible/scripts/vault-runtime.py" > "$work/runtime.json"
python3 - "$work/runtime.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["mail"]["password"] == "relay-secret", d
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

echo "OK: mail contract schema, validation, vault mapping, redaction and wiring seams"
