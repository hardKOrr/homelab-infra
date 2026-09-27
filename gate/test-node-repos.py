#!/usr/bin/env python3
"""Exercise bootstrap-rundeck.sh's node-repository helpers against fixture apt trees.

The helpers are Python heredocs inside the bootstrap script, because the script is copied
to a Proxmox node on its own and cannot import repository files. This test extracts each
heredoc verbatim, points it at a temporary apt directory instead of /etc/apt, and checks
the decisions the repository step depends on — above all the PVE 8 -> 9 upgrade case,
where every Proxmox entry still names bookworm on a trixie node.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = (ROOT / "rundeck/bootstrap-rundeck.sh").read_text(encoding="utf-8")

PVE = "http://download.proxmox.com/debian/pve"
CEPH_ANY = "http://download.proxmox.com/debian/ceph-*"


def helper(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n  python3 - [^\n]*<<'PY'\n(.*?)\nPY\n\}}", BOOTSTRAP, re.S | re.M)
    if match is None:
        raise AssertionError(f"{name} heredoc not found in bootstrap-rundeck.sh")
    return match.group(1)


class NodeRepoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.apt = Path(tmp.name)
        (self.apt / "sources.list.d").mkdir()
        (self.apt / "sources.list").write_text("")

    def run_helper(self, name: str, *args: str) -> subprocess.CompletedProcess:
        code = helper(name).replace("/etc/apt", self.apt.as_posix())
        return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True)

    def write(self, name: str, text: str) -> Path:
        path = self.apt / "sources.list.d" / name
        path.write_text(text)
        return path

    def active(self, uri: str, suite: str, component: str) -> bool:
        return self.run_helper("repo_active", uri, suite, component).returncode == 0

    def releases(self, codename: str) -> list[str]:
        return self.run_helper("ceph_enterprise_releases", codename).stdout.split()

    def test_upgrade_with_only_old_ceph_is_not_converted_and_warns(self):
        # A PVE 8 node upgraded to trixie: enterprise ceph-quincy for bookworm, nothing else.
        self.write("ceph.list", "deb https://enterprise.proxmox.com/debian/ceph-quincy bookworm enterprise\n")
        self.write("old.list", "deb http://download.proxmox.com/debian/ceph-quincy bookworm no-subscription\n")
        self.assertEqual(self.releases("trixie"), [], "a bookworm Ceph release was converted for trixie")
        self.run_helper("disable_stale_proxmox_suites", "trixie")
        # No enabled Ceph source for trixie remains, which is exactly the warning condition.
        self.assertFalse(self.active(CEPH_ANY, "trixie", "no-subscription"))

    def test_current_release_ceph_is_converted_even_when_already_disabled(self):
        self.write("ceph.sources", "Types: deb\nURIs: https://enterprise.proxmox.com/debian/ceph-squid\n"
                   "Suites: trixie\nComponents: enterprise\nEnabled: false\n")
        self.write("ceph-old.list", "# deb https://enterprise.proxmox.com/debian/ceph-quincy bookworm enterprise\n")
        self.assertEqual(self.releases("trixie"), ["squid"])

    def test_multi_suite_stanza_is_disabled_and_not_counted(self):
        path = self.write("proxmox.sources", f"Types: deb\nURIs: {PVE}\nSuites: bookworm trixie\n"
                          "Components: pve-no-subscription\n")
        self.assertFalse(self.active(PVE, "trixie", "pve-no-subscription"), "a mixed-suite stanza counted")
        self.run_helper("disable_stale_proxmox_suites", "trixie")
        self.assertIn("Enabled: false", path.read_text())

    def test_stale_list_disabled_debian_untouched_and_idempotent(self):
        path = self.write("mixed.list", f"deb {PVE} bookworm pve-no-subscription\n"
                          "deb http://deb.debian.org/debian trixie main\n"
                          f"deb {PVE} trixie pve-no-subscription\n")
        first = self.run_helper("disable_stale_proxmox_suites", "trixie")
        self.assertEqual(path.read_text().splitlines(), [
            f"# deb {PVE} bookworm pve-no-subscription",
            "deb http://deb.debian.org/debian trixie main",
            f"deb {PVE} trixie pve-no-subscription",
        ])
        self.assertIn("disabled", first.stdout)
        self.assertEqual(self.run_helper("disable_stale_proxmox_suites", "trixie").stdout, "")
        self.assertTrue(self.active(PVE, "trixie", "pve-no-subscription"))

    def test_inactive_entries_do_not_count(self):
        self.write("a.list", f"# deb {PVE} trixie pve-no-subscription\n")
        self.write("b.sources", f"Types: deb\nURIs: {PVE}\nSuites: trixie\n"
                   "Components: pve-no-subscription\nEnabled: no\n")
        self.assertFalse(self.active(PVE, "trixie", "pve-no-subscription"))
        self.write("c.list", f"deb [arch=amd64] {PVE} trixie pve-no-subscription\n")
        self.assertTrue(self.active(PVE, "trixie", "pve-no-subscription"))


if __name__ == "__main__":
    unittest.main()
