#!/usr/bin/env python3
"""Check that recovery primitives live only in their shared code or on a shrinking allowlist.

The test checks *where* a primitive appears, not how a role does the job. Each primitive
has the shared files allowed to contain it. Any other file containing it must be on that
primitive's frozen allowlist below, and an allowlisted file that no longer contains it
fails too, so the list can only shrink as roles move onto the shared code.

Mentions that do not count:
- Markdown files and any file named README*: prose, never executed.
- Full-line comments: a line whose first non-blank characters are `#` (YAML, shell,
  Python, and shell embedded in YAML block scalars) and Jinja `{# ... #}` comments,
  including multi-line ones. A trailing `# ...` after code is still code, because `#`
  also appears in URLs, `${#var}` and quoted strings.
"""

from fnmatch import fnmatch
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
# Directories that hold documentation, plans or this gate's own fixtures, not code.
SKIP_DIRS = ("gate/", "todo/", "docs/")

PBS_CLIENT_INSTALL = re.compile(
    r"install\b.*\bproxmox-backup-client"  # apt-get install ... proxmox-backup-client
    r"|\bname:\s*[\"']?proxmox-backup-client\b"  # ansible.builtin.apt name:
    r"|(?:proxmox-backup-client|pbs-client)\.list\b"  # apt source file
    r"|\bfilename:\s*\S*pbs-client"  # apt_repository filename:
    r"|backup\.client\.repository"  # rendering the configured client repository
)
DATABASE_DUMP = re.compile(
    r"\b(?:mysqldump|mariadb-dump|pg_dump|pg_restore|dropdb)\b|\bDROP\s+DATABASE\b",
    re.IGNORECASE,
)
K8S_JOB = re.compile(r"^\s*kind:\s*[\"']?(?:CronJob|Job)\b", re.MULTILINE)
PBS_CLIENT = re.compile(r"proxmox-backup-client")

# primitive: (description, shared files allowed to contain it, matcher)
PRIMITIVES = {
    "pbs-client-invocation": (
        "a proxmox-backup-client invocation",
        (
            "ansible/tasks/backup/pbs-client*.yml",
            "ansible/templates/kubernetes/recovery-*.yaml.j2",
        ),
        lambda path, code: bool(PBS_CLIENT.search(code)),
    ),
    "pbs-client-install": (
        "installing the PBS client package or repository",
        (
            "ansible/tasks/backup/pbs-client*.yml",
            "ansible/templates/kubernetes/recovery-*.yaml.j2",
        ),
        lambda path, code: bool(PBS_CLIENT_INSTALL.search(code)),
    ),
    "database-dump": (
        "mysqldump/mariadb-dump/pg_dump/pg_restore/DROP DATABASE/dropdb",
        ("ansible/tasks/database/*",),
        lambda path, code: bool(DATABASE_DUMP.search(code)),
    ),
    "k8s-recovery-job": (
        "a role template CronJob or Job that runs the PBS client",
        ("ansible/templates/kubernetes/recovery-*.yaml.j2",),
        lambda path, code: fnmatch(path, "ansible/roles/*/templates/*")
        and bool(K8S_JOB.search(code))
        and bool(PBS_CLIENT.search(code)),
    ),
}

# Frozen today. Parts 02-04 remove rows as roles move onto the shared code; never add one.
ALLOWLIST = {
    "pbs-client-invocation": {
        "ansible/roles/authentik/tasks/backup.yml",
        "ansible/roles/authentik/tasks/install-pbs-client.yml",
        "ansible/roles/authentik/tasks/restore.yml",
        "ansible/roles/bookstack/files/bookstack-recovery",
        "ansible/roles/bookstack/tasks/main.yml",
        "ansible/roles/forgejo/tasks/backup.yml",
        "ansible/roles/forgejo/tasks/restore.yml",
        "ansible/roles/immich/tasks/backup.yml",
        "ansible/roles/immich/tasks/restore.yml",
        "ansible/roles/influxdb/tasks/backup.yml",
        "ansible/roles/influxdb/tasks/install-pbs-client.yml",
        "ansible/roles/influxdb/tasks/restore.yml",
        "ansible/roles/jellyfin/tasks/backup.yml",
        "ansible/roles/jellyfin/tasks/restore.yml",
        "ansible/roles/karakeep/tasks/backup.yml",
        "ansible/roles/karakeep/tasks/restore.yml",
        "ansible/roles/mautic/tasks/backup.yml",
        "ansible/roles/mautic/tasks/restore.yml",
        "ansible/roles/n8n/tasks/backup.yml",
        "ansible/roles/n8n/tasks/restore-docker.yml",
        "ansible/roles/observability/tasks/backup.yml",
        "ansible/roles/observability/tasks/restore.yml",
        "ansible/roles/odoo/tasks/backup.yml",
        "ansible/roles/odoo/tasks/restore.yml",
        "ansible/roles/open-webui/files/open-webui-recovery",
        "ansible/roles/open-webui/tasks/restore.yml",
        "ansible/roles/plane/files/plane-recovery",
        "ansible/roles/plane/tasks/restore.yml",
        "ansible/roles/plex/tasks/backup.yml",
        "ansible/roles/plex/tasks/restore.yml",
        "ansible/roles/redis/tasks/backup-export.yml",
        "ansible/roles/redis/tasks/backup.yml",
        "ansible/roles/redis/tasks/install-pbs-client.yml",
        "ansible/roles/redis/tasks/main.yml",
        "ansible/roles/redis/tasks/restore.yml",
        "ansible/roles/vaultwarden/tasks/backup.yml",
        "ansible/roles/vaultwarden/tasks/install-pbs-client.yml",
        "ansible/roles/vaultwarden/tasks/main.yml",
        "ansible/roles/vaultwarden/tasks/restore.yml",
        "ansible/roles/wordpress/files/wordpress-recovery",
        "ansible/roles/wordpress/tasks/main.yml",
    },
    "pbs-client-install": {
        "ansible/roles/authentik/tasks/install-pbs-client.yml",
        "ansible/roles/bookstack/tasks/main.yml",
        "ansible/roles/forgejo/tasks/backup.yml",
        "ansible/roles/forgejo/tasks/restore.yml",
        "ansible/roles/immich/tasks/backup.yml",
        "ansible/roles/immich/tasks/restore.yml",
        "ansible/roles/influxdb/tasks/install-pbs-client.yml",
        "ansible/roles/jellyfin/tasks/backup.yml",
        "ansible/roles/jellyfin/tasks/restore.yml",
        "ansible/roles/karakeep/tasks/backup.yml",
        "ansible/roles/karakeep/tasks/restore.yml",
        "ansible/roles/mautic/tasks/backup.yml",
        "ansible/roles/mautic/tasks/restore.yml",
        "ansible/roles/n8n/tasks/backup.yml",
        "ansible/roles/n8n/tasks/restore-docker.yml",
        "ansible/roles/observability/tasks/backup.yml",
        "ansible/roles/observability/tasks/restore.yml",
        "ansible/roles/odoo/tasks/backup.yml",
        "ansible/roles/odoo/tasks/restore.yml",
        "ansible/roles/open-webui/files/open-webui-recovery",
        "ansible/roles/open-webui/tasks/main.yml",
        "ansible/roles/open-webui/tasks/restore.yml",
        "ansible/roles/open-webui/templates/open-webui-recovery.env.j2",
        "ansible/roles/plane/files/plane-recovery",
        "ansible/roles/plane/tasks/restore.yml",
        "ansible/roles/plane/templates/plane-recovery.env.j2",
        "ansible/roles/plex/tasks/backup.yml",
        "ansible/roles/plex/tasks/restore.yml",
        "ansible/roles/redis/tasks/install-pbs-client.yml",
        "ansible/roles/redis/tasks/main.yml",
        "ansible/roles/vaultwarden/tasks/install-pbs-client.yml",
        "ansible/roles/vaultwarden/tasks/main.yml",
        "ansible/roles/wordpress/tasks/main.yml",
    },
    "database-dump": set(),
    "k8s-recovery-job": set(),
}

JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)


def code_of(text):
    """Return text without Jinja comments or full-line `#` comments (see module docstring)."""
    # Keep the newlines a Jinja comment spanned so the remaining lines stay aligned.
    text = JINJA_COMMENT.sub(lambda match: "\n" * match.group(0).count("\n"), text)
    return "\n".join(
        "" if line.lstrip().startswith("#") else line for line in text.splitlines()
    )


def is_prose(path):
    return path.endswith(".md") or Path(path).name.startswith("README")


def tracked_files():
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    for name in output.decode().split("\0"):
        if name and not name.startswith(SKIP_DIRS) and not is_prose(name):
            yield name


def found_primitives():
    found = {primitive: set() for primitive in PRIMITIVES}
    for path in tracked_files():
        try:
            text = (ROOT / path).read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        code = code_of(text)
        for primitive, (_, _, matches) in PRIMITIVES.items():
            if matches(path, code):
                found[primitive].add(path)
    return found


def check_comment_handling():
    sample = "\n".join(
        [
            "# proxmox-backup-client in a YAML comment",
            "    # pg_dump in an indented shell comment",
            "{# mysqldump in a Jinja comment #}",
            "{# a multi-line Jinja comment",
            "   DROP DATABASE x; #}",
            "cmd: proxmox-backup-client backup  # trailing comment is code",
        ]
    )
    code = code_of(sample)
    assert PBS_CLIENT.search(code), "a call before a trailing comment must count"
    assert not DATABASE_DUMP.search(code), "comment-only database primitives must not count"
    assert code.count("proxmox-backup-client") == 1, "the full-line comment must not count"
    assert is_prose("ansible/README.md") and is_prose("ansible/tasks/wiring/README.md")
    assert not is_prose("ansible/roles/plane/files/plane-recovery")


def check_shared_globs():
    for primitive, (_, shared, _) in PRIMITIVES.items():
        for path in ALLOWLIST[primitive]:
            assert not any(fnmatch(path, pattern) for pattern in shared), (
                f"{primitive}: {path} is shared code; remove it from the allowlist"
            )


def main():
    check_comment_handling()
    check_shared_globs()
    failures = []
    for primitive, paths in found_primitives().items():
        description, shared, _ = PRIMITIVES[primitive]
        allowed = ALLOWLIST[primitive]
        for path in sorted(paths - allowed):
            if not any(fnmatch(path, pattern) for pattern in shared):
                failures.append(
                    f"{path}: contains {description}. Use the shared code "
                    f"({', '.join(shared)}) and supply per-app behavior through its "
                    f"hook or inputs; give the shared code a seam if it lacks one."
                )
        for path in sorted(allowed - paths):
            failures.append(
                f"{path}: no longer contains {description}; remove it from the "
                f"{primitive} allowlist in gate/test-shared-standards.py."
            )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    counts = ", ".join(f"{primitive} {len(ALLOWLIST[primitive])}" for primitive in PRIMITIVES)
    print(f"shared standards: no new copies; allowlisted {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
