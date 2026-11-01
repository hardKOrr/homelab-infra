#!/usr/bin/env python3
"""Render shared stack provisioning for both guest types and reject type drift."""
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / 'ansible/tasks/stack/find-or-create-host.yml').read_text())


def find(name, tasks=TASKS):
    for task in tasks:
        if task['name'] == name:
            return task
        if 'block' in task:
            try:
                return find(name, task['block'])
            except KeyError:
                pass
    raise KeyError(name)


def render(value, context):
    return Templar(loader=DataLoader(), variables=context).template(value)


def check():
    defaults = yaml.safe_load((ROOT / 'ansible/vars/homelabinfra-defaults.yml').read_text())
    base = defaults['homelabinfra_defaults']
    for kind in ('lxc', 'vm'):
        context = dict(
            homelabinfra_config=base,
            homelabinfra_defaults=base,
            _stack_sizing=dict(guest_type=kind, cores=4, memory=4096, disk_size=32),
            _stack_id='services-lxc', _stack_shared=False,
            _stack_resolution_device='', _stack_base_tags=['_+lab', '_-debian'],
            _stack_tags=['_+lab', '_-debian', '_-docker', '_.stack+services-lxc'],
            _stack_tag='_.stack+services-lxc',
        )
        task = find('Set stack LXC identity and sizing' if kind == 'lxc' else 'Set stack VM identity and sizing')
        config = render(task['ansible.builtin.set_fact']['homelabinfra_config'], context)
        guest = config['proxmox'][kind]
        assert guest['cores'] == 4 and guest['memory'] == 4096
        assert 'guest_type' not in guest
        assert '_.stack+services-lxc' in guest['tags']
        if kind == 'lxc':
            assert guest['unprivileged'] is True
            assert guest['features'] == ['nesting=1', 'keyctl=1']
            assert guest['disk_volume']['size'] == 32
            assert guest['ostemplate'] == base['proxmox']['lxc']['ostemplate']
        context.update(_stack_hosts=['stack-services-lxc'])
        assertion = find("Require the existing stack host's Proxmox node")['ansible.builtin.assert']['that'][1]
        for actual in ('lxc', 'qemu'):
            context['hostvars'] = {'stack-services-lxc': {'proxmox_vmtype': actual}}
            assert render('{{ ' + assertion + ' }}', context) == (actual == ('lxc' if kind == 'lxc' else 'qemu'))
    validation = find('Validate stack guest type and device compatibility')
    assert TASKS.index(validation) < next(i for i, task in enumerate(TASKS)
                                          if task['name'] == 'Refresh inventory to get current Proxmox state')
    for kind, gpu, devices, accepted in (
        ('vm', 'gpu', ['gpu'], True), ('lxc', '', [], True),
        ('lxc', 'gpu', [], False), ('lxc', '', ['device'], False),
        ('invalid', '', [], False),
    ):
        context = dict(_stack_sizing={'guest_type': kind},
                       _stack_resolution_device=gpu, stack_placement_devices=devices)
        assert all(render('{{ ' + rule + ' }}', context)
                   for rule in validation['ansible.builtin.assert']['that']) == accepted
    for name in ('Ensure the Debian cloud-init template exists for the stack VM', 'Clone the stack host VM'):
        assert find(name)['when'] == "_stack_sizing.guest_type == 'vm'"
    assert find('Create the stack host LXC')['ansible.builtin.include_tasks'] == '../proxmox/lxc-create.yml'
    print('stack-lxc: VM/LXC sizing, unprivileged template, dispatch and existing-host type checks passed')


if __name__ == '__main__':
    check()
