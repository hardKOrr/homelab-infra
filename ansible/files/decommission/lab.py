#!/usr/bin/env python3
"""Node-side decommission authority. No HTTP client, secret export or data erasure.

Ownership records are root-private, local to the creation node. Root is the trust
boundary: the record is not a signature and cannot protect against a malicious root.
Only owning creation seams call record(); an existing object is never adopted.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

RECORD = Path('/var/lib/homelab-infra/decommission-ownership.json')
KEYS = Path('/root/.ssh/authorized_keys')
OWNER = '_+lab'
KEY_COMMENT = 'homelab-infra platform key'
SOURCES = {'storage': 'configure-pbs', 'role': 'bootstrap-rundeck',
           'user': 'bootstrap-rundeck', 'token': 'bootstrap-rundeck',
           'acl': 'bootstrap-rundeck'}
SAFE = re.compile(r'^[A-Za-z0-9_.@!+-]+$')


class Refused(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Refused(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def command(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        require(proc.returncode == 0, 'Node command failed; inspect node privately and retry.')
        return proc.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused('Node command unavailable or timed out; no absence inferred.') from exc


def api(method, endpoint, **params):
    args = ['pvesh', method, endpoint, '--output-format', 'json']
    for key, value in params.items():
        args += ['--' + key.replace('_', '-'), str(value)]
    try:
        return json.loads(command(args) or 'null')
    except ValueError as exc:
        raise Refused('Invalid node response; no absence inferred.') from exc


def read_private(path, default=None):
    if not path.exists():
        require(default is not None, 'Required private handoff/ownership record is absent.')
        return default
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and info.st_mode & 0o077 == 0, 'Private record must be a root-owned regular 0600 file.')
    return json.loads(path.read_text())


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(not path.is_symlink(), 'Refuse symlink record.')
    tmp = path.with_name(path.name + '.new')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def objects():
    users = api('get', '/access/users')
    tokens = []
    for user in users:
        for token in api('get', '/access/users/' + user['userid'] + '/token'):
            tokens.append(dict(token, userid=user['userid']))
    return {'storage': api('get', '/storage'), 'role': api('get', '/access/roles'),
            'user': users, 'token': tokens, 'acl': api('get', '/access/acl'),
            'backup': api('get', '/cluster/backup')}


def identity(kind, row):
    return {'storage': lambda: row['storage'], 'role': lambda: row['roleid'],
            'user': lambda: row['userid'],
            'token': lambda: row['userid'] + '!' + row['tokenid'],
            'acl': lambda: row['path'] + '|' + row['type'] + '|' + row['ugid'] + '|' + row['roleid'],
            'backup': lambda: row['id']}[kind]()


def signature(kind, row):
    fields = {'storage': ['storage', 'type', 'server', 'datastore', 'username', 'fingerprint', 'content'],
              'role': ['roleid', 'privs'], 'user': ['userid', 'comment', 'enable', 'expire'],
              'token': ['userid', 'tokenid', 'privsep', 'expire', 'comment'],
              'acl': ['path', 'type', 'ugid', 'roleid', 'propagate']}[kind]
    return digest({field: row.get(field) for field in fields})


def record(kind, ident, source, node, state=None):
    require(SOURCES.get(kind) == source, 'Unknown owning creation seam.')
    state = objects() if state is None else state
    matches = [r for r in state[kind] if identity(kind, r) == ident]
    require(len(matches) == 1, 'Creation identity is absent or ambiguous.')
    existing = read_private(RECORD, {'schema': 1, 'node': node, 'objects': {}})
    require(existing.get('schema') == 1 and existing.get('node') == node, 'Ownership node/schema mismatch.')
    key = kind + ':' + ident
    stamp = {'source': source, 'identity': ident, 'signature': signature(kind, matches[0])}
    require(key not in existing['objects'] or existing['objects'][key] == stamp,
            'Existing creation record differs; never adopt/re-stamp it.')
    existing['objects'][key] = stamp
    write_private(RECORD, existing)


def owned_object(kind, row, records):
    ident = identity(kind, row)
    stamp = records.get('objects', {}).get(kind + ':' + ident, {})
    return (stamp.get('source') == SOURCES.get(kind) and stamp.get('identity') == ident
            and stamp.get('signature') == signature(kind, row))


def dedicated_user(userid, state, records):
    users = [u for u in state['user'] if u['userid'] == userid]
    return (len(users) == 1 and owned_object('user', users[0], records)
            and not users[0].get('groups')
            and all(owned_object('token', t, records) for t in state['token'] if t['userid'] == userid)
            and all(a.get('type') == 'user' and owned_object('acl', a, records)
                    for a in state['acl'] if a['ugid'] == userid))


def tags(row):
    return set(str(row.get('tags', '')).split(';'))


def guest_path(row):
    require(SAFE.fullmatch(row['node']) and row['type'] in ['lxc', 'qemu']
            and str(row['vmid']).isdigit(), 'Invalid guest identity.')
    return '/nodes/{node}/{type}/{vmid}'.format(**row)


def config(row):
    value = api('get', guest_path(row) + '/config')
    return {k: v for k, v in value.items() if k != 'digest'}


def disks(row, cfg):
    volumes, external = [], []
    for key, value in cfg.items():
        if re.fullmatch(r'(rootfs|mp\d+|unused\d+|scsi\d+|virtio\d+|ide\d+|sata\d+|efidisk\d+|tpmstate\d+)', key):
            volume = str(value).split(',')[0]
            if volume.startswith('file='):
                volume = volume[5:]
            if volume.startswith('/'):
                external.append({'device': key, 'source': volume, 'effect': 'reference removed; data/device preserved'})
            elif ':' in volume and volume not in ['none', 'cdrom'] and 'media=cdrom' not in str(value):
                volumes.append(volume)
        if re.fullmatch(r'(hostpci|usb|dev)\d+', key):
            external.append({'device': key, 'effect': 'binding removed; hardware/mapping preserved'})
    return sorted(set(volumes)), external


def volume_inventory(row):
    result = []
    for storage in api('get', '/nodes/' + row['node'] + '/storage'):
        # Disabled/inactive stores cannot prove absence after destruction.
        if 'images' in storage.get('content', '') or 'rootdir' in storage.get('content', ''):
            require(storage.get('active') == 1, 'Guest-volume storage inactive; cannot verify disks.')
            result += api('get', '/nodes/' + row['node'] + '/storage/' + storage['storage'] + '/content', vmid=row['vmid'])
    return sorted(r['volid'] for r in result if str(r.get('vmid')) == str(row['vmid']))


def canonical_keys(path=KEYS):
    if not path.exists():
        return []
    lines = path.read_text().splitlines(keepends=True)
    return [line for line in lines if key_owned(line)]


def key_owned(line):
    parts = line.strip().split(None, 2)
    return len(parts) == 3 and parts[0] in ['ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256',
                                          'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'] and parts[2] == KEY_COMMENT


def plan(node, runner):
    require(SAFE.fullmatch(node) and str(runner).isdigit(), 'Exact node and runner VMID required.')
    nodes = api('get', '/cluster/status')
    require(all(n.get('online') == 1 for n in nodes if n.get('type') == 'node'), 'All nodes must be online.')
    require(node in [n['name'] for n in nodes if n.get('type') == 'node'], 'Creation node not in cluster.')
    resources = api('get', '/cluster/resources', type='vm')
    guests, preserved = [], []
    for row in resources:
        cfg = config(row)
        require((OWNER in tags(row)) == (OWNER in tags(cfg)), 'Inventory/config ownership disagrees.')
        if OWNER not in tags(cfg):
            preserved.append({'identity': {k: row[k] for k in ['node', 'type', 'vmid', 'name']}, 'config_hash': digest(cfg)})
            continue
        require(not row.get('template') or '_.template' in tags(cfg), 'Owned template lacks template provenance.')
        volumes, external = disks(row, cfg)
        listed = volume_inventory(row)
        require(set(listed) == set(volumes), 'Orphan/unknown guest volumes require independent reconciliation.')
        guests.append({'identity': {k: row[k] for k in ['node', 'type', 'vmid', 'name']},
                       'config_hash': digest(cfg), 'volumes': volumes, 'external': external,
                       'tags': sorted(tags(cfg))})
    runners = [g for g in guests if str(g['identity']['vmid']) == str(runner)]
    require(len(runners) == 1 and '_rundeck' in runners[0]['tags'], 'Exact owned runner identity/tag required.')
    state = objects()
    records = read_private(RECORD, {'schema': 1, 'node': node, 'objects': {}})
    require(records.get('schema') == 1 and records.get('node') == node, 'Ownership record node/schema mismatch.')
    selected, excluded = [], []
    for kind, rows in state.items():
        for row in rows:
            ident = identity(kind, row)
            owned = (row.get('comment') in ['managed by homelab-infra', 'managed by homelab-infra — vmid list refreshed on bootstrap re-run']
                     if kind == 'backup' else owned_object(kind, row, records))
            if owned and kind == 'backup':
                selected_ids = set(re.split(r'[,;\s]+', str(row.get('vmid', '')))) - {''}
                owned_ids = {str(g['identity']['vmid']) for g in guests}
                owned = bool(selected_ids) and selected_ids <= owned_ids and not row.get('all') and not row.get('pool')
            if owned and kind == 'role':
                owned = all(a['ugid'] in [u['userid'] for u in state['user'] if owned_object('user', u, records)]
                            for a in state['acl'] if a['roleid'] == ident)
            if owned and kind in ['user', 'token', 'acl']:
                userid = ident if kind == 'user' else row.get('userid', row.get('ugid'))
                owned = dedicated_user(userid, state, records)
            if owned:
                selected.append({'kind': kind, 'identity': ident,
                                 'signature': digest(row) if kind == 'backup' else signature(kind, row)})
            else:
                excluded.append({'kind': kind, 'identity': ident, 'reason': 'unstamped, changed, shared or outside authority', 'state_hash': digest(row)})
    return {'schema': 1, 'node': node, 'runner': str(runner), 'guests': guests,
            'objects': selected, 'preserved_guests': preserved, 'excluded_objects': excluded,
            'keys_hash': digest(canonical_keys()),
            'external_review': 'Guest-mounted remote filesystems and guest-internal PBS datastores cannot be discovered from PVE config; operator must verify independent retention before confirming.',
            'retention': 'Independent artifacts/datastores, remote filesystems, physical storage and devices are never erased.',
            'handoff': 'Unwire every declared consumer while services/vault remain available; final execution runs as root on the creation PVE node, outside the runner.'}


def wait(node, upid):
    require(isinstance(upid, str) and upid.startswith('UPID:'), 'Mutation returned no task identity.')
    for _ in range(720):
        status = api('get', '/nodes/' + node + '/tasks/' + upid + '/status')
        if status.get('status') == 'stopped':
            require(status.get('exitstatus') == 'OK', 'Node task failed; retain handoff and retry after inspection.')
            return
        time.sleep(5)
    raise Refused('Node task timed out; inspect before retry.')


def current_guest(target):
    rows = api('get', '/cluster/resources', type='vm')
    matches = [r for r in rows if str(r['vmid']) == str(target['identity']['vmid'])]
    require(len(matches) <= 1, 'Ambiguous guest identity.')
    if not matches:
        return None
    row = matches[0]
    require(all(row.get(k) == v for k, v in target['identity'].items()), 'Guest identity moved or reused; re-plan.')
    cfg = config(row)
    require(OWNER in tags(row) and OWNER in tags(cfg) and digest(cfg) == target['config_hash'], 'Guest ownership/config changed; re-plan.')
    require(set(volume_inventory(row)) == set(target['volumes']), 'Guest volume inventory changed; re-plan.')
    return row


def verify_disks(target):
    require(not volume_inventory(target['identity']), 'Guest is absent but volumes remain; do not delete unknown leftovers.')


def destroy_guest(target):
    row = current_guest(target)
    if row:
        if api('get', guest_path(row) + '/status/current').get('status') != 'stopped':
            wait(row['node'], api('create', guest_path(row) + '/status/stop'))
        row = current_guest(target)  # destructive request gets a fresh ownership/config read
        require(row is not None, 'Guest vanished between stop and destruction; inspect disks and retry.')
        wait(row['node'], api('delete', guest_path(row), purge=0, destroy_unreferenced_disks=0))
    require(not any(str(r['vmid']) == str(target['identity']['vmid'])
                    for r in api('get', '/cluster/resources', type='vm')), 'Guest still present after task completion.')
    verify_disks(target)


def delete_object(target, records):
    kind, ident = target['kind'], target['identity']
    state = objects()  # re-read every object and sharing edge immediately before mutation
    rows = [r for r in state[kind] if identity(kind, r) == ident]
    if not rows:
        return
    require(len(rows) == 1, 'Ambiguous object identity.')
    row = rows[0]
    require((digest(row) if kind == 'backup' else signature(kind, row)) == target['signature'], 'Object changed; refuse deletion.')
    require(kind == 'backup' or owned_object(kind, row, records), 'Creation provenance missing/mismatched.')
    if kind in ['user', 'token', 'acl']:
        userid = ident if kind == 'user' else row.get('userid', row.get('ugid'))
        require(dedicated_user(userid, state, records), 'Credential user is adopted/shared or changed; refuse withdrawal.')
    if kind == 'user':
        require(not any(t['userid'] == ident for t in state['token']) and not any(a['ugid'] == ident for a in state['acl'])
                and not row.get('groups'), 'User still has credentials/ACL/group consumers.')
    if kind == 'role':
        require(not any(a['roleid'] == ident for a in state['acl']), 'Role still shared/referenced.')
    if kind == 'backup':
        require(row.get('comment') in ['managed by homelab-infra', 'managed by homelab-infra — vmid list refreshed on bootstrap re-run'],
                'Backup ownership marker changed.')
    if kind == 'storage':
        require(not any(j.get('storage') == ident for j in state['backup']), 'Storage still used by backup jobs.')
        require(row.get('type') == 'pbs', 'Only stamped PBS registration withdrawal supported.')
    paths = {'backup': '/cluster/backup/' + ident, 'storage': '/storage/' + ident,
             'user': '/access/users/' + ident, 'role': '/access/roles/' + ident}
    if kind == 'token':
        user, token = ident.split('!', 1)
        api('delete', '/access/users/' + user + '/token/' + token)
    elif kind == 'acl':
        require(row['type'] == 'user', 'Only exact user ACL withdrawal supported.')
        api('delete', '/access/acl', path=row['path'], users=row['ugid'], roles=row['roleid'])
    else:
        api('delete', paths[kind])
    require(not any(identity(kind, r) == ident for r in objects()[kind]), 'Object remains after withdrawal.')


def withdraw_keys(expected, path=KEYS):
    require(digest(canonical_keys(path)) == expected, 'Canonical platform keys changed; re-plan withdrawal.')
    if not path.exists():
        return
    # Write through the existing pmxcfs symlink, as bootstrap does. Never replace it.
    with path.open('r+') as stream:
        lines = stream.readlines()
        stream.seek(0)
        stream.writelines(line for line in lines if not key_owned(line))
        stream.truncate()
    require(not canonical_keys(path), 'Canonical platform keys remain.')


def verify_idle(node):
    nodes = api('get', '/cluster/status')
    require(all(n.get('online') == 1 for n in nodes if n.get('type') == 'node'), 'Node coverage incomplete; refuse execution.')
    for member in nodes:
        if member.get('type') == 'node':
            require(not api('get', '/nodes/' + member['name'] + '/tasks', source='active'),
                    'A node operation is active; wait for its final state.')
    require(node in [n['name'] for n in nodes if n.get('type') == 'node'], 'Creation node is absent.')


def execute(manifest, confirmation, retention, unwired, journal):
    require(confirmation == 'DECOMMISSION ' + digest(manifest), 'Literal plan-bound confirmation required.')
    require(retention == 'INDEPENDENT RETENTION VERIFIED' and unwired == 'ALL CONSUMERS UNWIRED',
            'Independent retention and completed provider handoff required.')
    require(os.geteuid() == 0 and Path('/etc/pve/local').exists(), 'Final execution must run as root on a PVE node outside runner.')
    require(os.uname().nodename == manifest['node'], 'Execute on the recorded creation node.')
    require(manifest.get('schema') == 1 and isinstance(manifest.get('consumers'), list),
            'Reviewed consumer declarations are missing from final plan.')
    declared = {c.get('instance') for c in manifest['consumers']}
    require('rundeck' in declared, 'Runner consumer handoff is missing.')
    planned_ids = [str(g['identity']['vmid']) for g in manifest['guests']]
    require(len(set(planned_ids)) == len(planned_ids), 'Plan has duplicate guest identities.')
    runners = [g for g in manifest['guests'] if str(g['identity']['vmid']) == manifest['runner']]
    require(len(runners) == 1 and '_rundeck' in runners[0]['tags'], 'Final plan has no exact runner identity.')
    for guest in manifest['guests']:
        require(all(t[1:] in declared for t in guest['tags'] if re.match(r'^_[A-Za-z0-9]', t)),
                'A workload has no declared consumer handoff.')
        current_guest(guest)
    require(all(str(r['vmid']) in planned_ids for r in api('get', '/cluster/resources', type='vm') if OWNER in tags(r)),
            'Owned resources appeared outside the plan; refuse execution.')
    done = read_private(journal, {'plan': digest(manifest), 'completed': []})
    require(done.get('plan') == digest(manifest), 'Journal belongs to another plan.')
    records = read_private(RECORD, {'schema': 1, 'node': manifest['node'], 'objects': {}})
    require(records.get('schema') == 1 and records.get('node') == manifest['node'], 'Provenance record changed.')
    require(KEYS.resolve() == Path('/etc/pve/priv/authorized_keys'),
            'Canonical keys are not cluster-shared; explicit per-node key handoff required.')
    def step(key, action):
        # Completed steps are also re-verified; no journal flag grants destruction authority.
        verify_idle(manifest['node'])
        action()
        if key not in done['completed']:
            done['completed'].append(key)
            write_private(journal, done)
    for kind in ['backup', 'storage']:
        for obj in manifest['objects']:
            if obj['kind'] == kind:
                step(kind + ':' + obj['identity'], lambda obj=obj: delete_object(obj, records))
    for guest in sorted(manifest['guests'], key=lambda g: str(g['identity']['vmid']) == manifest['runner']):
        step('guest:' + str(guest['identity']['vmid']), lambda guest=guest: destroy_guest(guest))
    require(not any(OWNER in tags(r) for r in api('get', '/cluster/resources', type='vm')), 'New platform guests appeared; re-plan before credential withdrawal.')
    for kind in ['token', 'acl', 'user', 'role']:
        for obj in manifest['objects']:
            if obj['kind'] == kind:
                step(kind + ':' + obj['identity'], lambda obj=obj: delete_object(obj, records))
    if 'keys' not in done['completed']:
        step('keys', lambda: withdraw_keys(manifest['keys_hash']))
    else:
        require(not canonical_keys(), 'Platform keys reappeared; re-plan.')
    for excluded in manifest['excluded_objects']:
        rows = [r for r in objects()[excluded['kind']] if identity(excluded['kind'], r) == excluded['identity']]
        require(len(rows) == 1 and digest(rows[0]) == excluded['state_hash'], 'Excluded object changed during teardown; investigate.')
    for preserved in manifest['preserved_guests']:
        require(digest(config(preserved['identity'])) == preserved['config_hash'], 'Unrelated guest changed during teardown; investigate.')
    return {'result': 'planned resources absent; excluded objects and independent data retained', 'completed': done['completed'],
            'excluded_objects': manifest['excluded_objects'],
            'operator_handoff': 'Revoke external provider/Vaultwarden credentials using their owning authority; retire private runner credential files only after independent retention review. No retained key/artifact deletion is automated.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True)
    parser.add_argument('--runner')
    parser.add_argument('--record', choices=SOURCES)
    parser.add_argument('--identity')
    parser.add_argument('--source')
    parser.add_argument('--execute', type=Path)
    parser.add_argument('--confirmation', default='')
    parser.add_argument('--retention', default='')
    parser.add_argument('--unwired', default='')
    parser.add_argument('--journal', type=Path, default=Path('/var/lib/homelab-infra/decommission-journal.json'))
    args = parser.parse_args()
    if args.record:
        require(os.geteuid() == 0, 'Creation records require node root authority.')
        record(args.record, args.identity, args.source, args.node)
        print('Creation identity recorded; no secret retained.')
    elif args.execute:
        value = read_private(args.execute)
        require(value.get('node') == args.node and value.get('schema') == 1, 'Manifest node/schema mismatch.')
        with Path('/var/lib/homelab-infra/decommission-operation.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Refused('Another node decommission owns the local lock.') from exc
            print(json.dumps(execute(value, args.confirmation, args.retention, args.unwired, args.journal)))
    else:
        value = plan(args.node, args.runner)
        print(json.dumps({'plan': value, 'confirmation': 'DECOMMISSION ' + digest(value)}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (Refused, ValueError, KeyError, TypeError) as exc:
        raise SystemExit('REFUSED: ' + str(exc))
