#!/usr/bin/env python3
"""Exercise the missing stack identity repair against socket-free PVE fixtures."""
import copy,json,os,subprocess,tempfile
from pathlib import Path
import yaml

R=Path(__file__).resolve().parent.parent
source=yaml.safe_load((R/'ansible/tasks/proxmox/repair-missing-stack-tag.yml').read_text())
fixture=r'''#!/usr/bin/env python3
import hashlib,json,os,sys
from pathlib import Path
root=Path(os.environ['REPAIR_FIXTURE']);args=sys.argv[1:]
if args[:2]==['get','/cluster/resources']:
 print(json.dumps(json.loads((root/'inventory.json').read_text())));sys.exit(0)
vmid=args[1].split('/')[-2];p=root/(vmid+'.json');cfg=json.loads(p.read_text())
if args[0]=='get':print(json.dumps(cfg));sys.exit(0)
if args[0]=='set':
 assert args[2]=='--tags' and args[4]=='--digest' and cfg['digest']==args[5]
 cfg['tags']=args[3];cfg['digest']=hashlib.sha256(args[3].encode()).hexdigest()
 p.write_text(json.dumps(cfg));(root/'writes').open('a').write(vmid+'\n');sys.exit(0)
raise SystemExit(2)
'''

def adapt(value,root):
 if isinstance(value,dict):
  result={k:adapt(v,root) for k,v in value.items()}
  if 'ansible.builtin.include_vars' in result:
   args=result['ansible.builtin.include_vars']
   if isinstance(args,dict) and isinstance(args.get('file'),str):
    args['file']=args['file'].replace('{{ playbook_dir }}/../../vars/stack-defaults.yml',str(R/'ansible/vars/stack-defaults.yml'))
  return result
 if isinstance(value,list):return [adapt(v,root) for v in value]
 if isinstance(value,str):return value.replace('/root/homelab-infra/missing-stack-tag-repair',str(root/'recovery'))
 return value

with tempfile.TemporaryDirectory() as t:
 root=Path(t);binary=root/'pvesh';binary.write_text(fixture);binary.chmod(0o700)
 cases=['plan','execute','repeat','shared-override','wrong-name','unowned','other-stack','template','duplicate','missing','locked','wrong-confirmation','missing-estate','wrong-estate']
 for case in cases:
  d=root/case;d.mkdir();(d/'pvesh').symlink_to(binary)
  stack_id='services' if case=='shared-override' else 'services-beta'
  expected_tag='_.stack+'+stack_id
  guest_name='stack-'+stack_id
  tags='_+lab;_-docker;operator-note'
  if case in ['repeat','other-stack']:tags+=';_.stack+services-beta'
  if case=='other-stack':tags+=';_.stack+services-alpha'
  if case=='shared-override':tags='_+lab;_-docker;operator-note'
  config={'hostname':guest_name,'tags':tags,'description':'operator note\n| mixpost | docker | internal | 2026-01-01 |','net0':'name=eth0,ip=192.0.2.10/24,hwaddr=00:11:22:33:44:55','digest':'fixture-revision','rootfs':'fixture-storage:vm-123-disk-0,size=8G'}
  inventory=[{'vmid':123,'node':'localhost','name':guest_name,'type':'lxc','tags':tags}]
  if case=='wrong-name':config['hostname']=inventory[0]['name']='stack-services-alpha'
  if case=='unowned':config['tags']=inventory[0]['tags']=config['tags'].replace('_+lab;','')
  if case=='other-stack':config['tags']=inventory[0]['tags']=tags
  if case=='template':inventory[0]['template']=1
  if case=='locked':config['lock']='backup'
  before=copy.deepcopy(config);(d/'123.json').write_text(json.dumps(config));(d/'inventory.json').write_text(json.dumps(inventory))
  play={'hosts':'localhost','gather_facts':False,'become':False,'connection':'local','vars':{'repair_execute':False},'tasks':adapt(copy.deepcopy(source),d)}
  infra_stacks={'services':{'shared':True}} if case=='shared-override' else {}
  play['vars'].update({'homelabinfra_config':{'proxmox':{'node':'localhost'},'infrastructure':{'stacks':infra_stacks,'domains':{'alpha':{},'beta':{}}}},'repair_stack':'services','repair_estate':'alpha' if case=='wrong-estate' else ('' if case in ['missing-estate','shared-override'] else 'beta'),'repair_vmids':'123,123' if case=='duplicate' else ('999' if case=='missing' else '123'),'repair_execute':case not in ['plan','missing-estate','wrong-estate'],'repair_confirmation':'WRONG' if case=='wrong-confirmation' else 'ADD STACK TAG '+expected_tag})
  # Notifications are outside the tag mutation contract and need a provider.
  for task in play['tasks']:
   if 'block' in task:task['block']=[x for x in task['block'] if 'ansible.builtin.include_tasks' not in x]
  path=d/'play.yml';path.write_text(yaml.safe_dump([play],sort_keys=False))
  env=dict(os.environ,REPAIR_FIXTURE=str(d),PATH=str(d)+os.pathsep+os.environ['PATH'],ANSIBLE_CONFIG=str(R/'ansible/ansible.cfg'),ANSIBLE_NOCOLOR='1')
  p=subprocess.run([str(Path.home()/'.venvs/homelab-ansible/bin/ansible-playbook'),'-i','localhost,',str(path)],env=env,text=True,capture_output=True,timeout=90)
  good=case in ['plan','execute','repeat','shared-override'];assert (p.returncode==0)==good,(case,p.stdout,p.stderr)
  after=json.loads((d/'123.json').read_text());writes=(d/'writes').read_text() if (d/'writes').exists() else ''
  if case in ['execute','shared-override']:
   assert writes=='123\n';expected=copy.deepcopy(before);expected['tags']=';'.join(sorted(set(before['tags'].split(';'))|{expected_tag}));expected['digest']=after['digest'];assert after==expected
   assert json.loads((d/'recovery/123.json').read_text())==before
  elif case=='repeat':assert after==before and not writes
  else:assert after==before and not writes,(case,after)
  print(case+': PASS')
print('missing stack identity tag repair tests passed')
