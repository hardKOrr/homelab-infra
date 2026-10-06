#!/usr/bin/env python3
"""Execute the source repair play against socket-free PVE command fixtures."""
import copy,json,os,subprocess,tempfile
from pathlib import Path
import yaml
R=Path(__file__).resolve().parent.parent
source=yaml.safe_load((R/'ansible/playbooks/maintenance/repair-orphan-stack-tag.yml').read_text())[0]
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
 if isinstance(value,dict):return {k:adapt(v,root) for k,v in value.items()}
 if isinstance(value,list):return [adapt(v,root) for v in value]
 if isinstance(value,str):return value.replace('/root/homelab-infra/tag-repair',str(root/'recovery'))
 return value
with tempfile.TemporaryDirectory() as t:
 root=Path(t);binary=root/'pvesh';binary.write_text(fixture);binary.chmod(0o700)
 for case in ['plan','execute','repeat','recorded','unowned','template','duplicate','missing','locked','wrong-confirmation']:
  d=root/case;d.mkdir();(d/'pvesh').symlink_to(binary)
  base={'tags':'_+lab;_-debian;_-docker;_.stack+sso;_authentik;_ntfy;operator-note','description':'operator note\n| authentik | docker | https://auth.example.com | 2026-01-01 |','net0':'name=eth0,ip=192.0.2.10/24,hwaddr=00:11:22:33:44:55','digest':'fixture-revision','rootfs':'fixture-storage:vm-123-disk-0,size=8G'}
  cfg=copy.deepcopy(base)
  inv=[{'vmid':123,'node':'localhost','name':'stack-sso','type':'lxc','tags':cfg['tags']}]
  if case=='recorded':cfg['description']+='\n| ntfy | docker | https://ntfy.example.com | 2026-01-01 |'
  if case=='unowned':inv[0]['tags']=cfg['tags']=cfg['tags'].replace('_+lab;','')
  if case=='template':inv[0]['template']=1
  if case=='locked':cfg['lock']='backup'
  if case=='repeat':cfg['tags']=cfg['tags'].replace(';_ntfy','');base=copy.deepcopy(cfg)
  before=copy.deepcopy(cfg);(d/'123.json').write_text(json.dumps(cfg));(d/'inventory.json').write_text(json.dumps(inv))
  play=adapt(copy.deepcopy(source),d);play.pop('pre_tasks');play['connection']='local';play['vars'].update({'homelabinfra_config':{'proxmox':{'node':'localhost'}},'repair_instance':'ntfy','repair_vmids':'123,123' if case=='duplicate' else ('999' if case=='missing' else '123'),'repair_execute':case!='plan','repair_confirmation':'WRONG' if case=='wrong-confirmation' else 'REMOVE ORPHAN TAG _ntfy'})
  # Notifications require no provider and are unrelated to the tag mutation contract.
  for task in play['tasks']:
   if 'block' in task:task['block']=[x for x in task['block'] if 'ansible.builtin.include_tasks' not in x]
  path=d/'play.yml';path.write_text(yaml.safe_dump([play],sort_keys=False))
  env=dict(os.environ,REPAIR_FIXTURE=str(d),PATH=str(d)+os.pathsep+os.environ['PATH'],ANSIBLE_CONFIG=str(R/'ansible/ansible.cfg'),ANSIBLE_NOCOLOR='1')
  p=subprocess.run(['ansible-playbook','-i','localhost,',str(path)],env=env,text=True,capture_output=True,timeout=90)
  good=case in ['plan','execute','repeat'];assert (p.returncode==0)==good,(case,p.stdout,p.stderr)
  after=json.loads((d/'123.json').read_text());writes=(d/'writes').read_text() if (d/'writes').exists() else ''
  if case=='execute':
   assert writes=='123\n';expected=copy.deepcopy(before);expected['tags']=expected['tags'].replace(';_ntfy','');expected['digest']=after['digest'];assert after==expected
   assert json.loads((d/'recovery/123.json').read_text())==before
  else:assert after==before and not writes,(case,after)
  print(case+': PASS')
print('orphan stack tag repair tests passed')
