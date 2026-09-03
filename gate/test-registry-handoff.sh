#!/usr/bin/env bash
# Producer -> consumer handoff for the generated service registry.
#
# tasks/bootstrap/write-generated-facts.yml is a PRODUCER: an app playbook's Play 3
# records its registry key and then wires against that key in the same play. This
# test EXECUTES that handoff instead of grepping for it, because the defect it
# guards was invisible to a source search: every caller followed the write with a
# "Reload infrastructure facts" include_vars, and include_vars ranks BELOW set_fact
# in Ansible's precedence order, so the reload silently kept the pre-write value.
# On a clean lab that made the first bootstrap pass skip each service's own wiring
# and look convergent only on the second run.
#
# It also pins the split the registry depends on: topology reaches the FILE, the
# vault secret already in memory must SURVIVE the merge, and no secret may be
# written to disk.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
work="$(mktemp -d "${TMPDIR:-/tmp}/homelab-registry-test.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT

fail() { echo "registry handoff test failed: $*" >&2; exit 1; }

playbook="${ANSIBLE_PLAYBOOK_BIN:-$HOME/.venvs/homelab-ansible/bin/ansible-playbook}"
[ -x "$playbook" ] || playbook="$(command -v ansible-playbook || true)"
[ -n "$playbook" ] && [ -x "$playbook" ] \
  || fail "no ansible-playbook interpreter (see gate/README.md bootstrap)"

# write-generated-facts.yml derives the repo root from generated_facts_file
# (dirname x3), so the fixture mirrors <root>/config/.generated + <root>/ansible.
mkdir -p "$work/config/.generated" "$work/ansible/scripts" "$work/ansible/tasks/bootstrap"
cp "$repo/ansible/scripts/secret-shape.py" "$work/ansible/scripts/"
cp "$repo/ansible/tasks/bootstrap/write-generated-facts.yml" \
   "$work/ansible/tasks/bootstrap/"
printf 'domain: example.com\n' > "$work/config/infrastructure.yml"

cat > "$work/ansible/handoff.yml" <<YAML
- hosts: localhost
  gather_facts: false
  tasks:
    # Stands in for load-user-vars.yml: homelabinfra_infra is established with
    # set_fact and already carries the credential half from the vault.
    - set_fact:
        homelabinfra_infra:
          monitoring:
            admin_password: from-vault

    - import_tasks: tasks/bootstrap/write-generated-facts.yml
      vars:
        generated_facts_file: $work/config/.generated/facts.yml
        generated_facts_service: monitoring
        generated_facts_data:
          provider: uptime_kuma
          host: "http://10.0.0.5:3001"
          admin_user: admin

    # Stands in for the wiring condition the same play evaluates next.
    - copy:
        dest: $work/consumed.json
        content: "{{ homelabinfra_infra | to_json }}"
YAML

( cd "$work/ansible" \
  && ANSIBLE_INVENTORY=localhost, \
     ANSIBLE_LOCALHOST_WARNING=False \
     ANSIBLE_INVENTORY_UNPARSED_WARNING=False \
     "$playbook" handoff.yml ) >"$work/run.log" 2>&1 \
  || { sed -n '1,60p' "$work/run.log" >&2; fail "handoff playbook did not complete"; }

python3 - "$work/consumed.json" "$work/config/.generated/facts.yml" <<'PY'
import json, sys, yaml

live = json.load(open(sys.argv[1]))["monitoring"]
disk = yaml.safe_load(open(sys.argv[2]))["monitoring"]

# The consumer must see what this play just produced.
for key, value in (("provider", "uptime_kuma"),
                   ("host", "http://10.0.0.5:3001"),
                   ("admin_user", "admin")):
    assert live.get(key) == value, \
        f"live registry lost {key}: {live!r} — the same-play write did not reach the consumer"

# ...without losing the credential the vault put there first.
assert live.get("admin_password") == "from-vault", \
    f"live registry dropped the vault credential: {live!r}"

# The file stays topology-only.
assert "admin_password" not in disk, \
    f"a credential reached config/.generated/facts.yml: {disk!r}"
assert disk.get("provider") == "uptime_kuma", f"file missing topology: {disk!r}"
PY

echo "registry handoff test passed"
