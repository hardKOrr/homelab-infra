#!/usr/bin/env python3
"""Render Forgejo's workload and verify portable, registered-database recovery."""
import os
import importlib.util
import subprocess
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ['ANSIBLE_CONFIG'] = str(ROOT / 'ansible/ansible.cfg')


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def render(path, variables):
    return Templar(loader=DataLoader(), variables=variables).template((ROOT / path).read_text())


def walk(tasks):
    for task in tasks:
        yield task
        for key in ('block', 'rescue', 'always'):
            yield from walk(task.get(key, []))


def main():
    defaults = load('ansible/vars/app-defaults/forgejo.yml')['forgejo_defaults']
    assert defaults['hosting']['method'] == 'docker'
    assert defaults['hosting']['supported'] == ['docker-vm', 'kubernetes']
    variables = {'instance': 'forgejo', 'forgejo_fqdn': 'forgejo.example.test',
                 'app_config': defaults, '_forgejo_k8s_env': {'USER_UID': '1000'}}
    env_vars = variables | {'ansible_managed': 'Ansible managed',
        'homelabinfra_infra': {'kubernetes': {'nodes': [{'ip_address': '192.0.2.21'}]}},
        'app_config': defaults | {'hosting': {'method': 'kubernetes'}, 'app': defaults['app'] | {
            'database_host': '192.0.2.30', 'database_port': 5432,
            'database_user': 'forgejo', 'database_password': 'fixture=value'}}}
    env_text = render('ansible/roles/forgejo/templates/forgejo.env.j2', env_vars)
    env = Templar(loader=DataLoader(), variables={'text': env_text}).template(
        "{{ dict(text.splitlines() | reject('match', '^#|^$') | map('split', '=', 1)) }}")
    assert env['FORGEJO__database__PASSWD'] == 'fixture=value'
    assert env['FORGEJO__server__SSH_DOMAIN'] == '192.0.2.21'
    assert env['FORGEJO__server__SSH_PORT'] == '30222'
    docs = list(yaml.safe_load_all(render('ansible/roles/forgejo/templates/manifest.yaml.j2', variables)))
    claim = next(d for d in docs if d['kind'] == 'PersistentVolumeClaim')
    assert claim['metadata']['name'] == 'forgejo-data'
    assert 'storageClassName' not in claim['spec']
    deployment = next(d for d in docs if d['kind'] == 'Deployment')
    assert deployment['spec']['strategy']['type'] == 'Recreate'
    pod = deployment['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert 'runAsUser' not in pod.get('securityContext', {})  # official image starts s6 as root
    app = pod['containers'][0]
    assert app['volumeMounts'] == [{'name': 'data', 'mountPath': '/data', 'subPath': 'forgejo'}]
    assert app['envFrom'] == [{'secretRef': {'name': 'forgejo-env'}}]
    assert app['readinessProbe']['httpGet'] == {'path': '/api/healthz', 'port': 3000}
    seed = pod['initContainers'][0]['args'][0]
    assert 'if [ ! -e /archive/forgejo/gitea/conf/app.ini ]; then' in seed
    assert 'forgejo generate secret SECRET_KEY' in seed
    subprocess.run(['sh', '-n'], input=seed, text=True, check=True)
    ssh = next(d for d in docs if d['kind'] == 'Service' and d['metadata']['name'].endswith('-ssh'))
    assert ssh['spec']['ports'][0]['nodePort'] == 30222
    ingress = next(d for d in docs if d['kind'] == 'Ingress')
    assert ingress['spec']['rules'][0]['host'] == 'forgejo.example.test'
    assert ingress['spec']['rules'][0]['http']['paths'][0]['path'] == '/'
    deploy = load('ansible/playbooks/apps/forgejo.yml')
    stack = next(t for t in deploy[0]['tasks'] if t['name'] == 'Find or create services stack host')
    assert stack['when'] == "app_config.hosting.method == 'docker'"
    kube = next(p for p in deploy if p['name'] == 'Forgejo | Deploy on Kubernetes')
    assert kube['tasks'][0]['when'] == "app_config.hosting.method == 'kubernetes'"
    wire = deploy[-1]
    assert 'ingress_vip' in wire['vars']['wiring_upstream_host']
    assert "80 if app_config.hosting.method == 'kubernetes'" in wire['vars']['wiring_upstream_port']
    endpoint = next(t for t in wire['tasks'] if t['name'] == 'Record Forgejo endpoint for app-to-app consumers')
    registration = Templar(loader=DataLoader(), variables=variables | {
        'app_config': defaults | {'hosting': {'method': 'kubernetes'}}
    }).template(endpoint['vars']['generated_facts_data'])
    assert registration['forgejo']['url'] == 'https://forgejo.example.test/'
    recovery = load('ansible/roles/forgejo/tasks/recovery-inputs.yml')
    assert recovery[0]['vars']['k8s_database_step_source'] == 'registered'
    db = {'dump': 'shared-dump', 'load': 'shared-load', 'image': 'postgres:17'}
    resolved = Templar(loader=DataLoader(), variables={'k8s_database_step': db} | recovery[1]['vars']).template(
        recovery[1]['ansible.builtin.set_fact']['_forgejo_k8s_database'])
    assert resolved['archive'] == 'data' and resolved['archive_path'] == 'database'
    assert resolved['dump'].startswith('shared-dump\n')
    assert '/volumes/data/database/forgejo.dump' in resolved['dump']
    assert resolved['load'].rstrip().endswith('shared-load')
    assert '${DUMP_DIR}/../forgejo/gitea/conf/app.ini' in resolved['load']
    spec = importlib.util.spec_from_file_location('shared_recovery_tests', ROOT / 'gate/test-k8s-recovery-jobs.py')
    shared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shared)
    real_db = shared.database_steps()['postgresql']
    portable_db = Templar(loader=DataLoader(), variables={
        'k8s_database_step': real_db, **recovery[1]['vars']
    }).template(recovery[1]['ansible.builtin.set_fact']['_forgejo_k8s_database'])
    pod, containers = shared.restore_pod('restore', [{'name': 'data', 'claim': 'forgejo-data', 'owner': '1000:1000'}], portable_db)
    scripts = '\n'.join(c['args'][0] for c in containers)
    assert '"${SNAPSHOT}" data.pxar /recovery/database' in scripts
    assert 'database.pxar /recovery/database' not in scripts
    load_container = next(c for c in containers if c['name'] == 'load')
    assert next(e['value'] for e in load_container['env'] if e['name'] == 'DUMP_DIR') == '/recovery/database/database'
    for container in containers:
        subprocess.run(['sh', '-n'], input=container['args'][0], text=True, check=True)
    _, cron = shared.backup_documents([{'name': 'data', 'claim': 'forgejo-data', 'dump_writable': True}], portable_db, stop=['forgejo'])
    dump_container = cron['spec']['jobTemplate']['spec']['template']['spec']['initContainers'][0]
    assert {'name': 'volume-data', 'mountPath': '/volumes/data'} in dump_container['volumeMounts']
    assert 'cp "${DUMP_DIR}/${DB_NAME}.dump" /volumes/data/database/forgejo.dump' in dump_container['args'][0]
    guard = next(t for t in load('ansible/tasks/kubernetes/recovery-restore.yml')
                 if t['name'] == 'k8s restore | Validate the database archive location')
    for archive, directory, accepted in [('data', 'database', True), ('database', '', True),
                                         ('data;id', 'database', False), ('data', '../database', False),
                                         ('data', '/database', False)]:
        context = {'k8s_recovery': {'database': {'archive': archive, 'archive_path': directory}}}
        checks = [Templar(loader=DataLoader(), variables=context).template('{{ ' + expr + ' }}')
                  for expr in guard['ansible.builtin.assert']['that']]
        assert all(checks) == accepted, (archive, directory)
    kube_tasks = load('ansible/roles/forgejo/tasks/kubernetes.yml')
    backup = next(t for t in kube_tasks if t.get('ansible.builtin.include_tasks', '').endswith('recovery-backup.yml'))
    assert backup['vars']['k8s_recovery']['archives'] == [
        {'name': 'data', 'claim': '{{ instance }}-data', 'dump_writable': True}]
    access = next(t for t in kube_tasks if t.get('ansible.builtin.include_tasks', '').endswith('open-app-access.yml'))
    assert access['vars']['firewall_inbound_ports'] == {'forgejo-ssh': '{{ app_config.app.ssh_node_port }}'}
    firewall = load('ansible/tasks/kubernetes/open-app-access.yml')[0]['block'][-1]
    upstream = firewall['vars']['wiring_upstream_host']
    cluster = {'homelabinfra_infra': {'kubernetes': {'ingress_vip': '192.0.2.20'}}}
    assert Templar(loader=DataLoader(), variables=cluster).template(upstream) == '192.0.2.20'
    assert Templar(loader=DataLoader(), variables=cluster | {'k8s_firewall_upstream_host': '192.0.2.21'}).template(upstream) == '192.0.2.21'
    assert any(t.get('ansible.builtin.include_tasks', '').endswith('claim-node-port.yml') for t in kube_tasks)
    restore = load('ansible/roles/forgejo/tasks/restore-kubernetes.yml')[-1]
    assert restore['ansible.builtin.include_tasks'].endswith('kubernetes/recovery-restore.yml')
    assert restore['vars']['k8s_recovery']['archives'][0]['owner'] == '1000:1000'
    dispatcher = load('ansible/roles/forgejo/tasks/main.yml')
    assert dispatcher[0]['when'] == "app_config.hosting.method | default('docker') == 'kubernetes'"
    assert dispatcher[1]['when'] == "app_config.hosting.method | default('docker') == 'docker'"
    assert dispatcher[1]['ansible.builtin.include_tasks'] == 'docker.yml'
    print('Forgejo Kubernetes workload and portable recovery checks passed')


if __name__ == '__main__':
    main()
