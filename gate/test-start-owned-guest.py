#!/usr/bin/env python3
"""Exercise exact owned stack guest starts against socket-free PVE fixtures."""
import json,os,subprocess,tempfile
from pathlib import Path
import yaml

R=Path(__file__).resolve().parent.parent
source=yaml.safe_load((R/'ansible/tasks/proxmox/start-owned-guest.yml').read_text())
pvesh=r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ['START_FIXTURE']);args=sys.argv[1:]
with (root/'pvesh-calls').open('a') as f:f.write(' '.join(args)+'\n')
if args[:2]==['get','/cluster/resources']:
 print(json.dumps(json.loads((root/'inventory.json').read_text())));sys.exit(0)
if args[:2]==['get','/nodes/localhost/status']:
 print(json.dumps({'memory':json.loads((root/'memory.json').read_text())}));sys.exit(0)
vmid=args[1].split('/')[-2];print(json.dumps(json.loads((root/(vmid+'.json')).read_text())))
'''
qm=r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
root=Path(os.environ['START_FIXTURE']);args=sys.argv[1:]
with (root/'commands').open('a') as f:f.write(' '.join(args)+'\n')
if args[0]=='start':(root/'started').write_text('yes')
sys.exit(0)
'''
cases=['plan','execute-lxc','execute-qemu','repeat-running','low-memory','low-memory-fallback','wrong-name','unowned','template','duplicate','missing','locked','wrong-confirmation']
with tempfile.TemporaryDirectory() as t:
 root=Path(t);pvesh_bin=root/'pvesh';pvesh_bin.write_text(pvesh);pvesh_bin.chmod(0o700)
 qm_bin=root/'qm';qm_bin.write_text(qm);qm_bin.chmod(0o700)
 for case in cases:
  d=root/case;d.mkdir();(d/'pvesh').symlink_to(pvesh_bin);(d/'qm').symlink_to(qm_bin);(d/'pct').symlink_to(qm_bin)
  name='stack-services';status='running' if case=='repeat-running' else 'stopped'
  vmtype='qemu' if case=='execute-qemu' else 'lxc'
  config_name_key='name' if vmtype=='qemu' else 'hostname'
  tags='_+lab;_-docker;_.stack+services;operator-note'
  config={config_name_key:name,'tags':tags,'description':'operator note','digest':'fixture-revision','memory':2048}
  inventory=[{'vmid':123,'node':'localhost','name':name,'type':vmtype,'status':status,'tags':tags}]
  if case=='wrong-name':inventory[0]['name']=config[config_name_key]='stack-other'
  if case=='unowned':inventory[0]['tags']=config['tags']=tags.replace('_+lab;','')
  if case=='template':inventory[0]['template']=1
  if case=='locked':config['lock']='backup'
  (d/'123.json').write_text(json.dumps(config));(d/'inventory.json').write_text(json.dumps(inventory))
  memory={'total':8*1024**3,'used':4*1024**3,'free':1024**3,'available':4*1024**3}
  if case in ['low-memory','low-memory-fallback']:
   memory.update({'used':7*1024**3,'free':4*1024**3})
  if case=='low-memory-fallback':memory.pop('available')
  elif case=='low-memory':memory['available']=1024**3
  (d/'memory.json').write_text(json.dumps(memory))
  tasks=[]
  for task in source:
   task=dict(task)
   if 'ansible.builtin.include_tasks' in task:
    task['ansible.builtin.include_tasks']=str(R/'ansible/tasks/proxmox/ensure-guest-running.yml')
   tasks.append(task)
  if case=='repeat-running':
   tasks.extend([
    {'ansible.builtin.add_host':{'name':'already-running','proxmox_vmid':'123','proxmox_node':'localhost','proxmox_status':'running','proxmox_vmtype':vmtype,'proxmox_name':name}},
    {'ansible.builtin.include_tasks':str(R/'ansible/tasks/proxmox/ensure-guest-running.yml'),'vars':{'ensure_guest_host':'already-running'}},
   ])
  play={'hosts':'localhost','gather_facts':False,'connection':'local','vars':{'start_guest_execute':False},'tasks':tasks}
  play['vars'].update({'homelabinfra_config':{'proxmox':{'node':'localhost','nodes':{},'api_host':'localhost'}},'start_guest_vmid':'999' if case=='missing' else ('123,123' if case=='duplicate' else '123'),'start_guest_name':name,'start_guest_execute':case not in ['plan','repeat-running'],'start_guest_confirmation':'WRONG' if case=='wrong-confirmation' else 'START OWNED GUEST 123'})
  path=d/'play.yml';path.write_text(yaml.safe_dump([play],sort_keys=False))
  env=dict(os.environ,START_FIXTURE=str(d),PATH=str(d)+os.pathsep+os.environ['PATH'],ANSIBLE_CONFIG=str(R/'ansible/ansible.cfg'),ANSIBLE_NOCOLOR='1')
  p=subprocess.run([str(Path.home()/'.venvs/homelab-ansible/bin/ansible-playbook'),'-i','localhost,',str(path)],env=env,text=True,capture_output=True,timeout=90)
  good=case in ['plan','execute-lxc','execute-qemu','repeat-running'];assert (p.returncode==0)==good,(case,p.stdout,p.stderr)
  calls=(d/'commands').read_text().splitlines() if (d/'commands').exists() else []
  api_calls=(d/'pvesh-calls').read_text().splitlines() if (d/'pvesh-calls').exists() else []
  if case=='execute-lxc':assert calls==['start 123','exec 123 -- /bin/true'] and (d/'started').exists(),calls
  elif case=='execute-qemu':assert calls==['start 123','guest exec 123 -- /bin/true'] and (d/'started').exists(),calls
  else:assert not calls,(case,calls)
  if case in ['low-memory','low-memory-fallback']:
   assert 'Not enough available memory for guest' in p.stdout,p.stdout
   assert 'stack-services' in p.stdout and "node 'localhost'" in p.stdout,p.stdout
   assert 'needs 2048 MiB' in p.stdout and 'only 1024 MiB available' in p.stdout,p.stdout
   assert any('/nodes/localhost/status' in call for call in api_calls),api_calls
  if case=='repeat-running':
   assert not any('/nodes/localhost/status' in call for call in api_calls),api_calls
  print(case+': PASS')
print('owned stack guest start tests passed')
