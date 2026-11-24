#!/usr/bin/env python3
"""Read-only migration preflight; stdin carries live PVE configuration and resources."""
import json
import re
import sys


def plan(data):
    source, target = data['source'], data['target']
    if not target or source == target or data.get('gpu', False):
        return {'mode': 'keep', 'node': source}
    config = data['config']
    tags = re.split(r'[;,\s]+', config.get('tags', ''))
    if '_+lab' not in tags or config.get('template', 0):
        raise ValueError('guest lacks exact _+lab ownership or is a template')
    if config.get('lock'):
        raise ValueError('guest is locked')
    for key, value in config.items():
        if re.fullmatch(r'(hostpci|usb|virtiofs|dev)\d+', key) or key in ('args', 'hookscript', 'ivshmem', 'vmstate'):
            raise ValueError(f'device or virtiofs mount pins the guest ({key})')
        if (re.fullmatch(r'(serial|parallel)\d+', key) and str(value).startswith('/')) or (
                key == 'rng0' and 'source=/dev/' in str(value)):
            raise ValueError(f'host device pins the guest ({key})')
        if key.startswith('lxc.'):
            raise ValueError(f'node-local LXC configuration pins the guest ({key})')
        if re.fullmatch(r'mp\d+', key) and str(value).split(',')[0].startswith('/'):
            raise ValueError(f'node-local bind mount pins the guest ({key})')
    disks = [(k, str(v).split(',')[0]) for k, v in config.items()
             if re.fullmatch(r'(scsi|sata|ide|virtio|unused|mp)\d+|rootfs|efidisk0|tpmstate0', k)]
    for key, volume in disks:
        if volume == 'none':
            continue
        storage = volume.split(':')[0] if ':' in volume else ''
        rows = [s for s in data['storages'] if s.get('storage') == storage]
        if len(rows) != 1 or not rows[0].get('shared'):
            raise ValueError(f'node-local or unknown storage pins the guest ({key}: {storage or "host device"})')
        if not rows[0].get('active') or not rows[0].get('enabled', 1):
            raise ValueError(f'shared storage is not active on destination ({key}: {storage})')
    sid = ('vm:' if data['type'] == 'qemu' else 'ct:') + str(data['vmid'])
    ha = [r for r in data['ha'] if r.get('sid') == sid]
    if ha and ha[0].get('state', 'started') != 'started':
        raise ValueError('HA resource must be in started state before migration')
    if ha and (len(ha) != 1 or ha[0].get('comment') != 'managed by homelab-infra'):
        raise ValueError('HA resource lacks homelab-infra ownership comment')
    return {'mode': 'ha' if ha else data['type'], 'node': target, 'sid': sid}


if __name__ == '__main__':
    try:
        print(json.dumps(plan(json.load(sys.stdin))))
    except (ValueError, KeyError, TypeError) as exc:
        sys.exit(f'Refusing pinned guest migration: {exc}.')
