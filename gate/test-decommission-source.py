#!/usr/bin/env python3
"""Run actual creation guards and preflight/refusal/handoff assertions offline."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path.home() / '.venvs/homelab-ansible/bin/python'
ANSIBLE = PYTHON.with_name('ansible-playbook')
spec = importlib.util.spec_from_file_location('decommission', ROOT / 'ansible/files/decommission/lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
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
source_block = next(t for t in unwire if 'block' in t)
guards = [t for t in block if 'ansible.builtin.assert' in t]
plan = {'guests': [{'tags': ['_+lab', '_ntfy', '_rundeck']}], 'node': 'fixture-node', 'runner': '902'}
base = dict(decommission_node='fixture-node', decommission_runner_vmid=902,
            decommission_unwire_phase='integrations',
            _dc_effect={'instance': 'ntfy'}, _dc_manifest={'wiring': [{'instance': 'ntfy'}]},
            _dc_forgejo_endpoint='',
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
run(checks, dict(base, decommission_apps='rundeck'), False)
run(checks, dict(base, decommission_apps=[{'app': 'rundeck', 'instance': 'rundeck'}]), False)
run(guards, base)
run(guards, dict(base, decommission_unwire_phase='proxy', _caddy_existing={}), False)
run(guards, dict(base, _ak_probe={'status': 403}), False)
run(guards, dict(base, kuma_call_ok=False), False)
run(guards, dict(base, _dc_manifest={'wiring': []}), False)
run(guards, dict(base, homelabinfra_infra={'dns': {'provider': 'invented'}}), False)
run(guards, dict(base, decommission_unwire_phase='dns',
                 homelabinfra_infra={'dns': {'provider': 'opnsense', 'host': 'https://dns.example.test'}}), False)
run(guards, dict(base, decommission_unwire_phase='dns',
                 homelabinfra_infra={'dns': {'provider': 'opnsense', 'host': 'https://192.0.2.53'}}))
providers = dict(base['homelabinfra_infra'], domain='example.test',
                 dns={'provider': 'opnsense', 'host': 'https://192.0.2.53'})
planned = run([source_block, {'ansible.builtin.debug': {'var': '_dc_wiring_plan'}}],
              dict(base, decommission_unwire_phase='plan', instance='ntfy',
                   decommission_app={'app': 'ntfy', 'instance': 'ntfy'},
                   app_config={'routing': {'subdomain': 'notify'}}, homelabinfra_infra=providers))
assert 'notify.example.test' in planned and 'remove only the instance' in planned, planned
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

# Exercise the actual authored global loops and stage conditions. Only included
# provider transports are replaced with recording tasks; no endpoint is contacted.
with tempfile.TemporaryDirectory() as temp:
    temp = Path(temp)
    recording = []
    for task in block:
        task = dict(task)
        if 'ansible.builtin.include_tasks' in task:
            route = str(task.pop('ansible.builtin.include_tasks'))
            task.pop('vars', None)
            task['ansible.builtin.debug'] = {'msg': 'RECORD {{ decommission_unwire_phase }} {{ decommission_app.instance }} ' + route}
        recording.append(task)
    recorded_block = dict(source_block, block=recording)
    (temp / 'unwire.yml').write_text(yaml.safe_dump([
        {'ansible.builtin.set_fact': {'instance': '{{ decommission_app.instance }}'}}, recorded_block]))
    phase = yaml.safe_load((ROOT / 'ansible/tasks/decommission/phase.yml').read_text())
    (temp / 'phase.yml').write_text(yaml.safe_dump(phase))
    orchestration = dict(by_name['Decommission | Unwire integrations before proxy routes and DNS'])
    orchestration['ansible.builtin.include_tasks'] = str(temp / 'phase.yml')
    providers = dict(base['homelabinfra_infra'], domain='example.test',
                     dns={'provider': 'opnsense', 'host': 'https://192.0.2.53'})
    replan = dict(orchestration, loop=['plan'])
    bind = {'ansible.builtin.set_fact': {'_dc_manifest': {'wiring': '{{ _dc_wiring_plan }}'}}}
    fixture = dict(base, decommission_phase='unwire', homelabinfra_infra=providers, app_config={'routing': {}})
    # Cross the real Ansible-to-node handoff: derive effects, bind both manifest
    # additions and render the authored confirmation, then hash the exported
    # object with the executor's implementation. Keep the port numeric so a
    # string coercion cannot silently pass this contract check.
    confirmation_path = temp / 'confirmation.json'
    numeric_providers = dict(providers, reverse_proxy={'provider': 'caddy', 'port': 2019})
    contract_plan = dict(plan, schema=1, exclusions={'external': ['retained café'], 'shared': False})
    contract_fixture = dict(fixture, homelabinfra_infra=numeric_providers,
                            _dc_plan={'stdout': json.dumps({'plan': contract_plan})})
    run([by_name['Decommission | Bind consumer declarations to the private plan'], replan,
         by_name['Decommission | Bind exact wiring effects to the private plan'],
         by_name['Decommission | Resolve literal plan confirmation'],
         {'ansible.builtin.copy': {
             'dest': str(confirmation_path), 'mode': '0600',
             'content': "{{ {'manifest': _dc_manifest, 'confirmation': _dc_confirmation} | to_json(sort_keys=True) }}"}}],
        contract_fixture)
    rendered = json.loads(confirmation_path.read_text())
    expected_wiring = [dict(
        app=consumer['app'], instance=consumer['instance'], domain=consumer['instance'] + '.example.test',
        reverse_proxy='caddy', sso='authentik', monitoring='uptime_kuma', dns='opnsense',
        targets={'reverse_proxy': '', 'reverse_proxy_port': 2019, 'sso': '', 'monitoring': '',
                 'dns': 'https://192.0.2.53'}, namespace='', forgejo='', forgejo_endpoint='',
        effect='remove only the instance route/SSO/monitor/DNS records and owned namespace; preserve dependencies and external data'
    ) for consumer in contract_fixture['decommission_apps']]
    expected_manifest = dict(contract_plan, consumers=contract_fixture['decommission_apps'], wiring=expected_wiring)
    assert rendered['manifest'] == expected_manifest, rendered
    assert type(rendered['manifest']['wiring'][0]['targets']['reverse_proxy_port']) is int, rendered
    assert rendered['confirmation'] == 'DECOMMISSION ' + lab.digest(rendered['manifest']), rendered
    assert rendered['confirmation'] == 'DECOMMISSION ' + lab.digest(expected_manifest), rendered
    print('decommission source: actual Ansible manifest/confirmation matches node digest with integer port')
    out = run([replan, bind, orchestration], fixture)
    events = re.findall(r'RECORD (integrations|proxy|dns) ([A-Za-z0-9_-]+)', out)
    assert len(events) == 8, out  # two integrations, one proxy and one DNS, for each consumer
    assert all(stage == 'integrations' for stage, _ in events[:4]), events
    assert all(stage == 'proxy' for stage, _ in events[4:6]), events
    assert all(stage == 'dns' for stage, _ in events[6:]), events
    changed = dict(providers, dns={'provider': 'opnsense', 'host': 'https://192.0.2.54'})
    out = run([replan, bind, {'ansible.builtin.set_fact': {'homelabinfra_infra': changed}}, orchestration], fixture, False)
    assert not re.search(r'RECORD (integrations|proxy|dns)', out), out
    forgejo_providers = dict(providers, apps={'forgejo-fixture': {'url': 'https://forgejo.example.test'}})
    forgejo_fixture = dict(fixture, homelabinfra_infra=forgejo_providers,
                           app_config={'routing': {}, 'app': {'forgejo': {'instance': 'forgejo-fixture'}}},
                           decommission_apps=[{'app': 'forgejo-runner', 'instance': 'fixture-runner'},
                                               {'app': 'rundeck', 'instance': 'rundeck'}])
    out = run([replan, bind, orchestration], forgejo_fixture)
    assert 'forgejo-runner-remove.yml' in out, out
    changed = dict(forgejo_providers, apps={'forgejo-fixture': {'url': 'https://changed.example.test'}})
    out = run([replan, bind, {'ansible.builtin.set_fact': {'homelabinfra_infra': changed}}, orchestration], forgejo_fixture, False)
    assert not re.search(r'RECORD (integrations|proxy|dns)', out), out
print('decommission source: authored global integration/proxy/DNS passes preserve provider ingress')
