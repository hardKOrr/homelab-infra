#!/usr/bin/env python3
"""Behavioral fixtures for backup evidence; no live endpoints or credentials."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('audit', Path(__file__).resolve().parents[1] / 'ansible/files/recovery/audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.guest = {'vmid': 123, 'name': 'example', 'node': 'pve-1', 'type': 'lxc', 'tags': '_+lab;_example'}
        self.job = {'id': 'nightly', 'all': 1, 'enabled': 1, 'schedule': '02:00', 'storage': 'pbs'}
        self.artifact = {'volid': 'pbs:backup/ct/123/2026-09-05T00:00:00Z', 'ctime': 1000, 'size': 100}

    def row(self, **kwargs):
        data = dict(guest=self.guest, jobs=[self.job], config={}, artifacts=[self.artifact], unreadable=[], now=1100, max_age=200)
        data.update(kwargs)
        return audit.report_guest(**data)

    def test_fresh_snapshot_never_proves_application_recovery(self):
        row = self.row()
        self.assertEqual(row['guest_snapshot'], 'fresh')
        for key in ['artifact_identity', 'artifact_integrity', 'application_consistency', 'restore_test', 'application_recoverability']:
            self.assertEqual(row[key], 'unverified')

    def test_missing_stale_future_and_unreadable_are_distinct(self):
        self.assertEqual(self.row(artifacts=[])['guest_snapshot'], 'missing')
        self.assertEqual(self.row(now=1300)['guest_snapshot'], 'stale')
        self.assertEqual(self.row(now=900)['guest_snapshot'], 'future')
        self.assertEqual(self.row(artifacts=[], unreadable=['store'])['guest_snapshot'], 'unknown')
        self.assertEqual(self.row(config=None)['config_read'], 'unknown')

    def test_wrong_type_and_vmid_do_not_match(self):
        for volid in ['pbs:backup/vm/123/time', 'pbs:backup/ct/1234/time']:
            self.assertEqual(self.row(artifacts=[dict(self.artifact, volid=volid)])['guest_snapshot'], 'missing')

    def test_latest_valid_time_wins(self):
        self.assertEqual(self.row(artifacts=[dict(self.artifact, ctime=950), self.artifact])['latest_candidate']['ctime'], 1000)
        for value in [None, 'bad', float('nan'), float('inf'), -1]:
            self.assertEqual(self.row(artifacts=[dict(self.artifact, ctime=value)])['guest_snapshot'], 'missing')

    def test_exact_ownership_tag(self):
        for tags in ['_+lab-extra', 'other_+lab', '_example', '']:
            self.assertEqual(self.row(guest=dict(self.guest, tags=tags))['ownership'], 'legacy')

    def test_job_selectors(self):
        for overrides in [{'exclude': '123'}, {'node': 'pve-2'}, {'all': 0, 'vmid': '1234,124'}]:
            self.assertFalse(audit.selects(dict(self.job, **overrides), self.guest))
        self.assertTrue(audit.selects(dict(self.job, all=0, vmid='122;123'), self.guest))
        self.assertIsNone(audit.selects(dict(self.job, pool='pool'), self.guest))

    def test_schedule_evidence(self):
        self.assertEqual(self.row()['schedule'], 'enabled')
        self.assertEqual(self.row(jobs=[])['schedule'], 'missing')
        self.assertEqual(self.row(jobs=None)['schedule'], 'unknown')
        self.assertEqual(self.row(jobs=[dict(self.job, enabled=0)])['schedule'], 'disabled')
        self.assertEqual(self.row(jobs=[dict(self.job, pool='pool')])['schedule'], 'unknown')

    def test_bind_mount_backup_flag_does_not_include_data(self):
        entries = audit.exclusions('lxc', {'mp0': '/data,mp=/appdata,backup=1', 'mp1': 'zfs:subvol-123-disk-1,mp=/included,backup=1', 'mp2': 'zfs:subvol-123-disk-2,mp=/excluded'})
        self.assertEqual([x['device'] for x in entries], ['mp0', 'mp2'])
        self.assertEqual(entries[0]['reason'], 'bind-or-device-mount')

    def test_vm_disks_and_external_devices(self):
        entries = audit.exclusions('qemu', {'scsi0': 'zfs:vm-123-disk-0,backup=0', 'scsi1': 'zfs:vm-123-disk-1', 'ide2': 'local:iso/install.iso,media=cdrom', 'hostpci0': 'SECRET', 'args': 'SECRET'})
        self.assertEqual({x['device'] for x in entries}, {'scsi0', 'hostpci0', 'args'})
        self.assertNotIn('SECRET', str(entries))

    def test_collector_failure_and_secret_redaction(self):
        def reader(path, **params):
            if path == '/cluster/resources':
                return [self.guest, dict(self.guest, vmid=124, tags='_+lab-extra')]
            if path == '/cluster/backup':
                raise audit.ReadError('SECRET')
            if path.endswith('/storage'):
                return [{'storage': 'pbs', 'active': 0}]
            return {'password': 'SECRET', 'hookscript': 'SECRET'}
        result = audit.collect('pve-1', reader=reader)
        self.assertEqual(len(result['guests']), 1)
        self.assertEqual(result['guests'][0]['schedule'], 'unknown')
        self.assertEqual(result['guests'][0]['guest_snapshot'], 'unknown')
        self.assertNotIn('SECRET', str(result))
        self.assertEqual(len(audit.collect('pve-1', include_legacy=True, reader=reader)['guests']), 2)


if __name__ == '__main__':
    unittest.main()
