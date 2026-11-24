#!/usr/bin/env python3
"""Render the example bootstrap through Ansible; exercise ownership and reuse guards."""
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from ansible.plugins.loader import init_plugin_loader
import yaml

ROOT = Path(__file__).resolve().parents[1]
os.environ['ANSIBLE_CONFIG'] = str(ROOT / 'ansible/ansible.cfg')
init_plugin_loader()


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def template(text, variables):
    return Templar(loader=DataLoader(), variables=variables).template(text)


def verify_hash(secret, encoded):
    assert encoded.startswith('$6$')
    result = subprocess.run(['openssl', 'passwd', '-6', '-salt', encoded.split('$')[2], '-stdin'],
                            input=secret, text=True, capture_output=True, check=True)
    return result.stdout.strip() == encoded


def render_example():
    defaults = load('ansible/vars/app-defaults/opnsense.yml')['opnsense_defaults']
    example = load('config.example/apps/opnsense.example.yml')
    config = template('{{ defaults | combine(example, recursive=True) }}',
                      {'defaults': defaults, 'example': example})
    credentials = {name: {'api_key': secrets.token_hex(40), 'api_secret': secrets.token_hex(40)}
                   for name in ('firewall-admin', 'firewall', 'dns')}
    credentials['apps'] = {'opnsense-drill': {'admin_password': secrets.token_hex(20)}}
    privileges = load('ansible/vars/opnsense-firewall-account.yml')
    hashes = {name: subprocess.run(['openssl', 'passwd', '-6', '-stdin'],
              input=value, text=True, capture_output=True, check=True).stdout.strip()
              for name, value in {'root': credentials['apps']['opnsense-drill']['admin_password'],
                                 **{k: v['api_secret'] for k, v in credentials.items() if k != 'apps'}}.items()}
    variables = {'_opn_boot_hashes': hashes, 'instance': 'opnsense-drill', 'app_config': config,
                 'homelabinfra_vault': credentials, **privileges}
    xml = template((ROOT / 'ansible/roles/opnsense/templates/config.xml.j2').read_text(), variables)
    tree = ET.fromstring(xml)
    assert tree.findtext('interfaces/wan/if') == 'vtnet0'
    assert tree.findtext('interfaces/lan/if') == 'vtnet1'
    assert tree.findtext('interfaces/lan/ipaddr') == example['bootstrap']['lan_address']
    assert tree.findtext('interfaces/lan/subnet') == str(example['bootstrap']['lan_prefix'])
    assert tree.findtext('system/webgui/interfaces') == 'lan'
    assert tree.find('trigger_initial_wizard') is None
    root = tree.find("system/user[name='root']")
    assert verify_hash(credentials['apps']['opnsense-drill']['admin_password'], root.findtext('password'))
    for service, name, expected in (
        ('firewall-admin', 'homelab-infra-admin', ['page-all']),
        ('firewall', 'homelab-infra-firewall', privileges['opnsense_firewall_account_privileges']),
        ('dns', 'homelab-infra-dns', ['page-services-unbound']),
    ):
        user = tree.find(f"system/user[name='{name}']")
        assert user.findtext('descr') == 'Owned by homelab-infra'
        assert [p.text for p in user.findall('priv')] == expected
        assert user.find('groupname') is None
        assert user.findtext('apikeys/item/key') == credentials[service]['api_key']
        assert verify_hash(credentials[service]['api_secret'], user.findtext('apikeys/item/secret'))
        assert credentials[service]['api_secret'] not in xml
    for task in load('ansible/roles/opnsense/tasks/validate.yml'):
        for expression in task['ansible.builtin.assert']['that']:
            assert template('{{ ' + expression + ' }}', variables) is True, expression
    broken = template('{{ config | combine({"proxmox": {"installer": {"lan_bridge": config.proxmox.installer.wan_bridge}}}, recursive=True) }}', {'config': config})
    assert template('{{ app_config.proxmox.installer.wan_bridge != app_config.proxmox.installer.lan_bridge }}',
                    {'app_config': broken}) is False
    return xml


def ownership():
    tasks = load('ansible/tasks/proxmox/disk-appliance-create.yml')
    guard = next(t for t in tasks if t['name'].endswith('Refuse a foreign VMID or a different placement'))
    vm = {'vmid': 500, 'name': 'opnsense-drill'}
    guest = {'vmid': 500, 'name': vm['name'], 'type': 'qemu', 'node': 'fixture-node',
             'tags': '_+lab;_opnsense-drill', 'template': 0}

    def allowed(matches):
        variables = {'_disk_appliance_matches': matches, 'appliance_vm': vm,
                     'homelabinfra_config': {'proxmox': {'node': 'fixture-node'}}}
        return all(template('{{ ' + expr + ' }}', variables) is True
                   for expr in guard['ansible.builtin.assert']['that'])

    assert allowed([]) and allowed([guest])
    for difference in ({'name': 'other'}, {'tags': '_opnsense-drill'},
                       {'tags': '_+lab;_other'}, {'node': 'other-node'},
                       {'type': 'lxc'}, {'template': 1}):
        assert not allowed([{**guest, **difference}]), difference
    assert not allowed([guest, guest])
    seed = next(t for t in load('ansible/roles/opnsense/tasks/main.yml')
                if t['name'].endswith('Seed and import a new boot disk'))
    assert template('{{ ' + seed['when'] + ' }}',
                    {'_disk_appliance_config': {'scsi0': 'fixture:vm-500-disk-0'}}) is False
    assert template('{{ ' + seed['when'] + ' }}', {'_disk_appliance_config': {}}) is True

    config = {'name': vm['name'], 'tags': guest['tags'], 'cores': 2, 'memory': 4096,
              'agent': '0', 'onboot': 1, 'net0': 'virtio=00:00:00:00:00:01,bridge=vmbr0',
              'net1': 'virtio=00:00:00:00:00:02,bridge=vmbr1',
              'scsi0': 'fixture:vm-500-disk-0,size=120G', 'boot': 'order=scsi0'}
    identity = {**vm, 'cores': 2, 'memory': 4096, 'storage': 'fixture',
                'net0': 'virtio,bridge=vmbr0', 'net1': 'virtio,bridge=vmbr1'}
    wiring_guard = tasks[-1]['ansible.builtin.assert']['that']

    def wiring_allowed(difference):
        variables = {'_disk_appliance_config': {**config, **difference}, 'appliance_vm': identity}
        return all(template('{{ ' + expr + ' }}', variables) is True for expr in wiring_guard)

    assert wiring_allowed({})
    for difference in ({'lock': 'backup'}, {'net0': 'virtio=00:00:00:00:00:01,bridge=vmbr9'},
                       {'net1': 'virtio=00:00:00:00:00:02,bridge=vmbr1,tag=42'},
                       {'hostpci0': 'fixture-pci'}, {'net2': 'virtio,bridge=vmbr0'},
                       {'unused0': 'fixture:vm-500-disk-1'}, {'boot': 'order=ide2;scsi0'}):
        assert not wiring_allowed(difference), difference

    capacity = load('ansible/tasks/proxmox/disk-appliance-resize.yml')
    for size, grow in (('3G', True), ('120G', False), ('256G', False), ('122880M', False)):
        variables = {'appliance_vm': {'disk_size': 120},
                     '_disk_appliance_after_import': {'stdout': __import__('json').dumps(
                         {'scsi0': f'fixture:vm-500-disk-0,size={size}'})}}
        variables['_disk_appliance_bytes'] = template(
            capacity[1]['ansible.builtin.set_fact']['_disk_appliance_bytes'],
            {**variables, '_disk_size_parts': template(capacity[1]['vars']['_disk_size_parts'], variables)})
        assert template('{{ ' + capacity[2]['when'] + ' }}', variables) is grow


def reconciliation_scope():
    """Exercise include resolution and precedence over the loader's set_fact values."""
    executable = os.environ.get('GATE_ANSIBLE_PLAYBOOK') or shutil.which('ansible-playbook')
    assert executable, 'ansible-playbook executable is not configured'
    original = {'infrastructure': {'firewall': {'base': {
        'enabled': True, 'aliases': {'keep': ['192.0.2.10']},
        'rules': [{'interface': 'opt4'}]}}}, 'unrelated': 'keep'}
    infra = {'firewall': {'host': 'estate.invalid'}, 'dns': {'host': 'estate.invalid'}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        files = {
            'roles/opnsense/tasks/main.yml': load('ansible/roles/opnsense/tasks/configure.yml')[1:],
            'tasks/firewall/opnsense-base.yml': [{
                'ansible.builtin.assert': {'that': [
                    "homelabinfra_config.infrastructure.firewall.base.rules[0].interface == expected_interface",
                    "homelabinfra_config.infrastructure.firewall.base.aliases.keep == ['192.0.2.10']",
                    "homelabinfra_config.unrelated == 'keep'",
                    "homelabinfra_infra.firewall.host == '192.0.2.1'",
                ]}}],
        }
        for name in ('opnsense-firewall-account', 'opnsense-dns-account'):
            files[f'tasks/wiring/{name}.yml'] = [{
                'ansible.builtin.assert': {'that': [
                    "homelabinfra_infra.firewall.host == '192.0.2.1'",
                    "homelabinfra_infra.dns.host == '192.0.2.1'",
                ]}}]
        for path, value in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.safe_dump(value, sort_keys=False))
        for override in (False, True):
            bootstrap = {'lan_address': '192.0.2.1', 'validate_certs': False}
            if override:
                bootstrap['firewall_base'] = {'rules': [{'interface': 'lan'}]}
            play = [{'hosts': 'localhost', 'gather_facts': False, 'vars': {
                'app_config': {'bootstrap': bootstrap},
                'expected_interface': 'lan' if override else 'opt4',
            }, 'tasks': [
                {'ansible.builtin.set_fact': {'homelabinfra_config': original, 'homelabinfra_infra': infra}},
                {'ansible.builtin.include_role': {'name': 'opnsense'}},
                {'ansible.builtin.assert': {'that': [
                    'homelabinfra_config == original', 'homelabinfra_infra == original_infra']},
                 'vars': {'original': original, 'original_infra': infra}},
            ]}]
            path = root / 'play.yml'
            path.write_text(yaml.safe_dump(play, sort_keys=False))
            result = subprocess.run([executable, '-i', 'localhost,', '-c', 'local', str(path)],
                                    text=True, capture_output=True, env={**os.environ,
                                    'ANSIBLE_ROLES_PATH': str(root / 'roles')})
            assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    render_example()
    ownership()
    reconciliation_scope()
    print('OPNsense bootstrap: example XML, credential hashes, ownership and disk preservation passed')
