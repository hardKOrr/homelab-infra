#!/usr/bin/env python3
"""Read-only Proxmox guest backup evidence. Never claims application recoverability.

Run on a PVE node with permission to read cluster resources, jobs, guest config and
storage content. Only GET requests are issued. Raw configs and command errors never
leave the collector; they may contain secrets. No third-party Python dependencies.
"""
import argparse
import datetime
import json
import math
import re
import subprocess


class ReadError(Exception):
    """A read failed; its raw output must not be reported."""


def get(path, **params):
    argv = ['pvesh', 'get', path, '--output-format', 'json']
    for key, value in params.items():
        argv.extend(['--' + key, str(value)])
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise ReadError(path)
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise ReadError(path) from exc


def enabled(value, default=True):
    if value is None:
        return default
    return str(value).lower() in ('1', 'true', 'yes', 'on')


def ids(value):
    return set(re.split(r'[,;\s]+', str(value or '').strip())) - {''}


def selects(job, guest):
    """True/False/None: None means the selector requires unavailable evidence."""
    vmid = str(guest['vmid'])
    if job.get('node') and job['node'] != guest['node']:
        return False
    if vmid in ids(job.get('exclude')):
        return False
    if job.get('pool'):
        return None
    if enabled(job.get('all'), False):
        return True
    if job.get('vmid'):
        return vmid in ids(job['vmid'])
    return None


def properties(value):
    parts = str(value).split(',')
    result = {}
    for i, part in enumerate(parts):
        if '=' in part:
            key, val = part.split('=', 1)
            result[key] = val
        elif i == 0:
            result['volume'] = part
    return result


def exclusions(kind, config):
    """Report data outside vzdump; do not infer paths are empty or dispensable."""
    findings = []
    for key, value in sorted(config.items()):
        if kind == 'lxc' and re.fullmatch(r'mp\d+', key):
            props = properties(value)
            volume = props.get('volume', '')
            if volume.startswith('/'):
                reason = 'bind-or-device-mount'
            elif not enabled(props.get('backup'), False):
                reason = 'volume-backup-disabled'
            else:
                continue
            findings.append({'device': key, 'target': props.get('mp'), 'reason': reason})
        elif kind == 'qemu' and re.fullmatch(r'(?:scsi|sata|virtio|ide)\d+', key):
            props = properties(value)
            volume = props.get('file', props.get('volume', ''))
            if props.get('media') == 'cdrom' or volume.endswith(':cloudinit'):
                continue
            if not enabled(props.get('backup')):
                findings.append({'device': key, 'reason': 'disk-backup-disabled'})
            elif volume.startswith('/dev/'):
                findings.append({'device': key, 'reason': 'physical-disk-review-required'})
        elif key in ('lxc.mount.entry', 'lxc.mount', 'hookscript', 'args') or re.fullmatch(r'(?:hostpci|usb|unused)\d+', key):
            findings.append({'device': key, 'reason': 'external-resource-review-required'})
    return findings


def timestamp(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def report_guest(guest, jobs, config, artifacts, unreadable, now, max_age):
    vmid = str(guest['vmid'])
    kind = 'ct' if guest['type'] == 'lxc' else 'vm'
    selected = [j for j in jobs or [] if selects(j, guest) is True]
    active = [j for j in selected if enabled(j.get('enabled')) and j.get('schedule')]
    uncertain = jobs is None or any(selects(j, guest) is None for j in jobs)
    state = 'enabled' if active else ('unknown' if uncertain else ('disabled' if selected else 'missing'))
    candidates = []
    for item in artifacts:
        # Match the backup type as well as VMID. Historical VMIDs can be reused;
        # this establishes a candidate, never proof of source application identity.
        if re.fullmatch(r'[^:]+:backup/' + kind + '/' + re.escape(vmid) + r'/[^/]+', str(item.get('volid', ''))):
            captured = timestamp(item.get('ctime'))
            if captured:
                candidates.append((captured, item))
    latest = max(candidates, key=lambda pair: pair[0]) if candidates else None
    artifact_state = 'unknown' if unreadable else 'missing'
    artifact = None
    if latest:
        captured, item = latest
        age = now - captured
        artifact_state = 'future' if age < 0 else ('fresh' if age <= max_age else 'stale')
        artifact = {key: item.get(key) for key in ('volid', 'ctime', 'size')}
        artifact['age_hours'] = round(age / 3600, 2)
    review = exclusions(guest['type'], config) if config is not None else None
    return {
        'vmid': guest['vmid'], 'name': guest.get('name'), 'node': guest['node'],
        'type': guest['type'],
        'ownership': 'managed' if '_+lab' in str(guest.get('tags', '')).split(';') else 'legacy',
        'schedule': state,
        'jobs': [{k: j.get(k) for k in ('id', 'storage', 'schedule', 'mode', 'enabled')} for j in selected],
        'guest_snapshot': artifact_state, 'latest_candidate': artifact,
        'config_read': 'ok' if config is not None else 'unknown',
        'external_or_excluded_data': review,
        'artifact_identity': 'unverified', 'artifact_integrity': 'unverified',
        'application_consistency': 'unverified', 'restore_test': 'unverified',
        'application_recoverability': 'unverified',
    }


def collect(node, include_legacy=False, max_age_hours=36, reader=get):
    guests = reader('/cluster/resources', type='vm')
    if not isinstance(guests, list):
        raise ReadError('/cluster/resources')
    errors = []
    try:
        jobs = reader('/cluster/backup')
        if not isinstance(jobs, list):
            raise ReadError('/cluster/backup')
    except ReadError:
        jobs = None
        errors.append('backup-jobs-unreadable')
    artifacts = []
    try:
        storages = reader('/nodes/' + node + '/storage', content='backup')
        if not isinstance(storages, list):
            raise ReadError('storage-list')
    except ReadError:
        storages = []
        errors.append('storage-list-unreadable')
    for storage in storages:
        name = storage['storage']
        if not enabled(storage.get('active'), False):
            errors.append('storage-unavailable:' + name)
            continue
        try:
            contents = reader('/nodes/' + node + '/storage/' + name + '/content', content='backup')
            if not isinstance(contents, list):
                raise ReadError('storage-content')
            artifacts.extend(contents)
        except ReadError:
            errors.append('storage-content-unreadable:' + name)
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    rows = []
    for guest in sorted(guests, key=lambda g: int(g['vmid'])):
        if enabled(guest.get('template'), False):
            continue
        if not include_legacy and '_+lab' not in str(guest.get('tags', '')).split(';'):
            continue
        try:
            config = reader('/nodes/' + guest['node'] + '/' + guest['type'] + '/' + str(guest['vmid']) + '/config')
            if not isinstance(config, dict):
                raise ReadError('guest-config')
        except ReadError:
            config = None
        rows.append(report_guest(guest, jobs, config, artifacts, errors, now, max_age_hours * 3600))
    return {
        'schema_version': 1, 'observed_at': int(now), 'storage_view_node': node,
        'max_age_hours': max_age_hours, 'read_errors': errors,
        'scope': 'all-guests' if include_legacy else 'managed-guests',
        'limitations': [
            'Storage visibility is limited to the selected node; other stores may hold artifacts.',
            'A snapshot candidate does not prove identity, integrity, application consistency or restore success.',
            'Guest config does not enumerate application databases, secrets or guest-mounted external storage.',
            'Pool selectors require a separate membership check.',
        ],
        'guests': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True)
    parser.add_argument('--include-legacy', action='store_true')
    parser.add_argument('--max-age-hours', type=float, default=36)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', args.node):
        parser.error('invalid Proxmox node name')
    if not math.isfinite(args.max_age_hours) or args.max_age_hours <= 0:
        parser.error('max-age-hours must be positive and finite')
    try:
        result = collect(args.node, args.include_legacy, args.max_age_hours)
    except ReadError:
        parser.exit(1, 'Cannot read the Proxmox guest inventory; recovery coverage is unknown.\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
