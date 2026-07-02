#!/bin/bash
# Syntax-check gate: --syntax-check over every playbook under ansible/playbooks/, without
# contacting Proxmox. Invoked by .claude/build.yml's `test:` wrapper from the repo root:
#   wsl bash -lc 'cd <repo-root> && bash .claude/gate/test.sh'
set -uo pipefail

cd ansible

# See .claude/gate/lint.sh for the ANSIBLE_CONFIG world-writable-directory rationale.
export ANSIBLE_CONFIG=/mnt/c/Users/korr/source/repos/homelab-infra/ansible/ansible.cfg

# Neutralise the Proxmox dynamic inventory (see lint.sh) so no live inventory is touched.
export ANSIBLE_INVENTORY=localhost,

mapfile -t playbooks < <(find playbooks -name "*.yml")

# Refuse to report success on an empty check set: an unexpanded find (e.g. a shell-relay
# quoting hazard that mangles the command before it reaches WSL) must fail loudly, not
# silently run zero iterations and exit 0.
if [ "${#playbooks[@]}" -eq 0 ]; then
    echo "ERROR: find playbooks -name *.yml matched zero files; refusing to report a false pass." >&2
    exit 1
fi

echo "Found ${#playbooks[@]} playbook(s) to syntax-check."

rc=0
for pb in "${playbooks[@]}"; do
    echo "== $pb"
    "$HOME/.venvs/homelab-ansible/bin/ansible-playbook" --syntax-check -i localhost, "$pb" || rc=1
done
exit $rc
