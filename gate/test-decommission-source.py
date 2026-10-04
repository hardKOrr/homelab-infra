#!/usr/bin/env python3
"""Run actual creation guards and preflight/refusal/handoff assertions offline."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path.home() / '.venvs/homelab-ansible/bin/python'
ANSIBLE = PYTHON.with_name('ansible-playbook')
source = (ROOT / 'rundeck/bootstrap-rundeck.sh').read_text()
start = source.index('if pveum role list --output-format json', source.index('record_created_pve_object()'))
end = source.index('\n#', source.index('token ${PVE_USER}!${PVE_TOKEN_NAME} already exists', start))
creation = source[start:end]
# Execute the real guards, supplying only shell-local recording transports.
with tempfile.TemporaryDirectory() as temp:
    events = Path(temp) / 'events'
    stub = r'''
PVE_ROLE=HomelabInfra PVE_USER=homelab-infra@pve PVE_TOKEN_NAME=automation
PVE_PRIVS=VM.Allocate ROTATE_PROXMOX_TOKEN=0
info() { :; }
die() { exit 19; }
record_created_pve_object() { printf '%s\n' "$1:$2" >> "$EVENTS"; }
pveum() {
  case "$*" in
    'role list --output-format json') [ "$EXISTS" = 0 ] || printf '[{"roleid":"HomelabInfra"}]';;
    'user list --output-format json') [ "$EXISTS" = 0 ] || printf '[{"userid":"homelab-infra@pve"}]';;
    'user token list homelab-infra@pve --output-format json') [ "$EXISTS" = 0 ] || printf '[{"tokenid":"automation"}]';;
    'user token add homelab-infra@pve automation --privsep 0 --output-format json') printf '{"value":"fixture-placeholder"}';;
    *) :;;
  esac
}
pvesh() {
  if [ "$EXISTS" = 1 ]; then
    printf '[{"path":"/","type":"user","ugid":"homelab-infra@pve","roleid":"HomelabInfra"}]'
  else printf '[]'; fi
}
'''
    for exists in ['0', '1']:
        events.write_text('')
        subprocess.run(['bash', '-euc', stub + creation], check=True,
                       env=dict(os.environ, EVENTS=str(events), EXISTS=exists))
        recorded = events.read_text().splitlines()
        if exists == '0':
            assert recorded == ['role:HomelabInfra', 'user:homelab-infra@pve',
                                'acl:/|user|homelab-infra@pve|HomelabInfra',
                                'token:homelab-infra@pve!automation'], recorded
        else:
            assert not recorded, 'Existing objects must never acquire provenance on rerun'

# PBS uses the exact pre-POST absence observation to gate BOTH creation and stamping.
# Evaluate that authored condition through Ansible, not an independent Python predicate.
pbs = yaml.safe_load((ROOT / 'ansible/tasks/bootstrap/configure-pbs.yml').read_text())
create = next(t for t in pbs if t['name'] == 'PBS | Register PBS as a PVE storage backend')
stamp = next(t for t in pbs if t['name'] == 'PBS | Record newly created registration identity')
assert create['when'] == stamp['when']
play = yaml.safe_load((ROOT / 'ansible/playbooks/maintenance/decommission.yml').read_text())[0]
by_name = {t['name']: t for t in play['pre_tasks'] + play['tasks']}
unwire = yaml.safe_load((ROOT / 'ansible/tasks/decommission/unwire.yml').read_text())
block = next(t['block'] for t in unwire if 'block' in t)
guards = [t for t in block if 'ansible.builtin.assert' in t]
plan = {'guests': [{'tags': ['_+lab', '_ntfy', '_rundeck']}], 'node': 'fixture-node', 'runner': '902'}
base = dict(decommission_node='fixture-node', decommission_runner_vmid=902,
            decommission_apps=[{'app': 'ntfy', 'instance': 'ntfy'}, {'app': 'rundeck', 'instance': 'rundeck'}],
            _dc_plan={'stdout': json.dumps({'plan': plan})}, _luv_config_dir='/fixture-no-config',
            homelabinfra_infra={'reverse_proxy': {'provider': 'caddy'}, 'sso': {'provider': 'authentik'},
                               'monitoring': {'provider': 'uptime_kuma'}, 'dns': {'provider': 'none'}},
            _caddy_existing={'status': 404}, _ak_probe={'status': 200}, kuma_session_ready=True, kuma_call_ok=True)


def run(tasks, variables, expected=True):
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'probe.yml'
        path.write_text(yaml.safe_dump([dict(name='Actual source decommission checks', hosts='localhost', gather_facts=False,
            vars=variables, tasks=[{'ansible.builtin.add_host': {'name': 'fixture-node', 'groups': 'proxmox_delegates'}}] + tasks)]))
        proc = subprocess.run([str(ANSIBLE), '-i', 'localhost,', '-c', 'local', str(path)],
                              capture_output=True, text=True, cwd=ROOT / 'ansible', timeout=60)
        assert (proc.returncode == 0) == expected, proc.stdout + proc.stderr
        return proc.stdout

checks = [by_name[n] for n in ['Decommission | Require exact bounded phase and declarations',
          'Decommission | Bind consumer declarations to the private plan',
          'Decommission | Resolve literal plan confirmation',
          'Decommission | Require every recorded workload to have an explicit declaration',
          'Decommission | Require complete user configuration coverage']]
run(checks, base)
run(checks, dict(base, decommission_phase='execute'), False)
run(checks, dict(base, decommission_apps=[{'app': 'rundeck', 'instance': 'rundeck'}]), False)
run(guards, base)
run(guards, dict(base, _caddy_existing={}), False)
run(guards, dict(base, _ak_probe={'status': 403}), False)
run(guards, dict(base, kuma_call_ok=False), False)
run(guards, dict(base, homelabinfra_infra={'dns': {'provider': 'invented'}}), False)
confirmation = by_name['Decommission | Require plan-bound literal confirmation and retention handoff']
run(checks[:3] + [confirmation], dict(base, decommission_phase='unwire', decommission_confirmation='yes'), False)
handoff = by_name['Decommission | Handoff successful unwiring to independent operator']
output = run([handoff], dict(base, decommission_phase='unwire'))
assert 'ALL CONSUMERS UNWIRED' in output and 'authority' in output and 'creation PVE node' in output
for rows, expected in [([], True), ([{'storage': 'pbs-homelab'}], False)]:
    out = run([{'ansible.builtin.debug': {'msg': 'NEW REGISTRATION ONLY'}, 'when': stamp['when']}],
              {'_pve_storages': {'json': {'data': rows}}})
    assert ('NEW REGISTRATION ONLY' in out) == expected
print('decommission source: new creation/no-adoption, PBS guards, literal confirmation, provider refusal, workload coverage and outside-runner handoff passed')
