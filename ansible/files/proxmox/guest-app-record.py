#!/usr/bin/env python3
"""Record or withdraw one app instance in a Proxmox guest's tags and notes.

Runs ON a Proxmox node (Ansible delegates it there), because the read this needs is only
available from the node: `pct config` percent-encodes the description onto a single line and
PVE 9.2 rejects `--output-format`, so the decoded text can only be got from
`pvesh get /nodes/<node>/<type>/<vmid>/config --output-format json`. The write goes through
the same endpoint, so read and write are symmetric and one code path serves lxc and qemu.

MANY WRITERS, ONE FIELD
    Every app deployed onto a shared stack host mutates the same guest's description and tag
    list, and a removal must withdraw one app's entry while its siblings stay byte-identical.
    So the write is read-modify-write keyed by instance: parse the guest's current state,
    replace only this instance's row, and render both the description and the managed tags
    from the resulting rows. A blind append is the bug that once gave a second stack host the
    first one's stack tag, after which both stacks resolved to whichever host inventory
    listed first.

    Deriving the tags from the table is what makes them survive a shared host: a removal
    withdraws exactly the rows that left and nothing else, and the guest's ownership,
    machine-fact and topology tags are never candidates for removal at all.

IDEMPOTENCY
    Rows are emitted sorted by instance so a re-run never reshuffles the table, and an
    existing row keeps its recorded date when nothing else about it changed. Without that,
    every re-deploy would rewrite the date, no deploy would ever be a no-op, and the guest
    config would gain a new revision on every run. `deployed` therefore reads as "when this
    record was first written or last changed", not "when the playbook last ran".

Exit status is 0 for every outcome the caller is expected to survive — a guest that has
vanished, a config that cannot be read, a refused update. This is bookkeeping, not the
deploy, and it must never fail an otherwise successful run. The outcome is reported on
stdout as one of:

    CHANGED <detail>   the guest config was updated
    OK <detail>        already correct, nothing written
    SKIPPED <detail>   the guest could not be found or read
    FAILED <detail>    Proxmox refused the update
"""

import argparse
import datetime
import json
import re
import subprocess
import sys

MARKER_START = "<!-- homelab-infra:apps -->"
MARKER_END = "<!-- /homelab-infra:apps -->"
HEADING = "### Apps (managed by homelab-infra)"
TABLE_HEAD = "| instance | kind | url | deployed |"
TABLE_RULE = "|---|---|---|---|"
EMPTY_CELL = "-"

# The exact ownership sentinel. Membership of it is the only test for "this platform created
# this guest"; see ansible/inventory/proxmox.yml for the whole tag grammar.
OWNER_TAG = "_+lab"


def pvesh(args):
    """Run pvesh and return (rc, stdout, stderr). Never raises for a non-zero exit."""
    proc = subprocess.run(
        ["pvesh"] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def find_guest(name, vmid):
    """Resolve a guest to (vmid, node, type) from the cluster-wide resource list.

    Looked up by name rather than by hostvars, because the deploy group's member is an
    add_host entry: a guest created earlier in the same run carries no inventory facts at
    all, so proxmox_vmid/proxmox_node are absent on exactly the first-deploy path that most
    needs recording. The cluster resource list is authoritative for both cases and also
    tells us which node holds the guest, which need not be the node we are delegated to.
    """
    rc, out, err = pvesh(["get", "/cluster/resources", "--type", "vm", "--output-format", "json"])
    if rc != 0:
        return None, "cluster resource list unavailable: %s" % err.strip()
    try:
        resources = json.loads(out)
    except ValueError as exc:
        return None, "cluster resource list unparsable: %s" % exc

    for res in resources:
        if vmid and str(res.get("vmid")) == str(vmid):
            return res, None
    if vmid:
        return None, "no guest with vmid %s" % vmid

    matches = [r for r in resources if r.get("name") == name]
    if not matches:
        return None, "no guest named %s" % name
    # A name collision across the cluster is resolved toward the guest this system owns.
    # EXACT membership of the ownership sentinel, never a prefix test: every platform tag
    # starts with `_`, and `_` alone means "platform lane", not "ours".
    owned = [r for r in matches if OWNER_TAG in (r.get("tags") or "").split(";")]
    return (owned or matches)[0], None


def read_config(guest):
    path = "/nodes/%s/%s/%s/config" % (guest["node"], guest["type"], guest["vmid"])
    rc, out, err = pvesh(["get", path, "--output-format", "json"])
    if rc != 0:
        return None, "config unreadable: %s" % err.strip()
    try:
        return json.loads(out), None
    except ValueError as exc:
        return None, "config unparsable: %s" % exc


def split_region(description):
    """Return (before, region_body, after) around the managed markers.

    Text on both sides of the region is pre-existing operator content and is handed back
    untouched — including anything below the end marker, which a naive split would drop.
    """
    if MARKER_START not in description:
        return description.strip(), "", ""
    before, _, rest = description.partition(MARKER_START)
    body, _, after = rest.partition(MARKER_END)
    return before.strip(), body, after.strip()


def parse_rows(body):
    """Parse the region's table into {instance: [instance, kind, url, deployed]}."""
    rows = {}
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|-"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 4 or cells[0] in ("", "instance"):
            continue
        rows[cells[0]] = cells
    return rows


def render_region(rows):
    lines = [MARKER_START, HEADING, TABLE_HEAD, TABLE_RULE]
    lines += ["| %s |" % " | ".join(rows[key]) for key in sorted(rows)]
    lines.append(MARKER_END)
    return "\n".join(lines)


def compose(before, region, after):
    return "\n\n".join([part for part in (before, region, after) if part]).strip()


def cell(value):
    """Table cells never contain a pipe, which would silently add a column."""
    return (value or "").replace("|", "/").strip() or EMPTY_CELL


def merge_rows(description, instance, kind, url, date, withdraw):
    """Apply this instance's change to the guest's table, returning the surrounding text.

    The row set is the single source of truth for both writes: the description renders from
    it, and so do the managed tags. One parse, one merge, two renderings that cannot disagree.

    The PREVIOUS row set is returned alongside the new one, because a withdrawal must remove
    exactly the application tags the rows it deleted produced — never everything that looks
    like an application tag, and never anything matched on a bare `_` prefix.
    """
    before, body, after = split_region(description)
    previous = parse_rows(body)
    rows = dict(previous)

    if withdraw:
        rows.pop(instance, None)
    else:
        wanted = [instance, cell(kind), cell(url), date]
        current = rows.get(instance)
        # Keep the recorded date when only the date would differ, so a re-deploy of an
        # unchanged app writes nothing at all.
        if current and current[:3] == wanted[:3]:
            wanted[3] = current[3]
        rows[instance] = wanted

    return before, previous, rows, after


def render_description(before, rows, after):
    # An emptied region leaves no markers and no heading behind — a bare table header on a
    # guest that runs nothing is worse than no region at all.
    return compose(before, render_region(rows) if rows else "", after)


def tag_list(tags):
    """Normalise PVE's tag field to a sorted, deduplicated list.

    Both sides of the change comparison go through this, because clearing the last tag leaves
    PVE reporting a single space rather than an empty string. Comparing the raw field against
    a rendered one would then differ forever and rewrite the guest config on every run.
    """
    return sorted({t.strip() for t in (tags or "").split(";") if t.strip()})


# PVE accepts these in a tag on top of the alphanumerics and `_`. The platform grammar uses
# all three: `_+lab` for ownership, `_-debian` for a machine fact, `_.stack+media` for
# topology. Stripping them, as this once did, collapsed three lanes into one.
TAG_ALPHABET = r"[^A-Za-z0-9_+.-]"

# The lanes the platform reserves after the leading underscore. An application tag is
# `_<instance>`, so an instance whose name began with one of these would be indistinguishable
# from ownership, a machine fact or a topology tag — and a withdrawal keyed on the
# application lane would then delete a tag the guest needs. The check refuses such a name
# outright rather than mangling it into something that looks fine and means something else.
RESERVED_LANES = ("+", "-", ".")


def tag_token(value):
    """PVE tags accept a restricted alphabet; anything else becomes an underscore."""
    return re.sub(TAG_ALPHABET, "_", value)


def app_tag(instance):
    """The tag for one application instance: its canonical name with one leading underscore."""
    return tag_token("_%s" % instance)


def reserved_name(instance):
    """True when this instance name would collide with a reserved platform lane."""
    return instance.startswith(RESERVED_LANES) or instance.startswith("_")


def merge_tags(tags, previous_rows, rows):
    """Render the application tags from the merged row set, never a blind append.

    ONE family is managed here — `_<instance>` — and nothing else is ever a candidate for
    removal. The guest keeps its ownership tag `_+lab`, its machine facts (`_-debian`,
    `_-docker`, `_-k3s`), its topology (`_.stack+media`, `_.cluster+k3s`, `_.template`), its
    `_.shared` declaration and every hand-applied operator tag. Deleting on a bare `_`
    prefix would take all of those with it, which is why the removal set is computed from
    the rows that were actually withdrawn:

        removable = application tags of the PREVIOUS row set - application tags of the new one

    That is what keeps a withdrawal correct on a shared host and on a cluster: removing one
    app from a stack host leaves its siblings' tags byte-identical, and removing the last one
    leaves the stack's own identity intact. A blind append is the bug that once gave a second
    stack host the first one's stack tag.
    """
    managed = {app_tag(instance) for instance in rows}
    removable = {app_tag(instance) for instance in previous_rows} - managed
    kept = [t for t in tag_list(tags) if t not in removable]
    # PVE stores tags sorted; sorting here keeps the comparison stable.
    # Plain lexicographic order IS the reading order the grammar was designed for: ASCII puts
    # `+` (43) before `-` (45) before `.` (46) before a letter, so a sorted tag list reads
    # ownership, machine facts, topology, then applications, with any operator tag that does
    # not start with `_` after them. No comparator is needed and none should be added.
    return ";".join(sorted(set(kept) | managed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--guest", default="", help="guest hostname to resolve")
    parser.add_argument("--vmid", default="", help="guest vmid; overrides --guest when set")
    parser.add_argument(
        "--kind", default="native", choices=["docker", "native", "kubernetes"]
    )
    parser.add_argument("--url", default="")
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--withdraw", action="store_true", help="remove the record instead")
    args = parser.parse_args()

    if not args.guest and not args.vmid:
        print("SKIPPED no guest identity supplied")
        return 0

    # Refused, not sanitised. An instance called `.media` would produce the tag `_.media`,
    # which reads as a topology tag and would be invisible to the application lane that has
    # to withdraw it later. Nothing generates such a name today; this is the guard that keeps
    # it that way, and it fires before anything is written.
    if reserved_name(args.instance):
        print(
            "SKIPPED instance %r starts with a reserved platform lane (%s or _)"
            % (args.instance, ", ".join(RESERVED_LANES))
        )
        return 0

    guest, problem = find_guest(args.guest, args.vmid)
    if problem:
        print("SKIPPED %s" % problem)
        return 0

    config, problem = read_config(guest)
    if problem:
        print("SKIPPED %s" % problem)
        return 0

    description = config.get("description", "") or ""
    tags = config.get("tags", "") or ""

    before, previous_rows, rows, after = merge_rows(
        description, args.instance, args.kind, args.url, args.date, args.withdraw
    )
    new_description = render_description(before, rows, after)
    new_tags = merge_tags(tags, previous_rows, rows)

    if new_description == description.strip() and new_tags == ";".join(tag_list(tags)):
        state = "already absent from" if args.withdraw else "already recorded on"
        print("OK %s %s %s" % (args.instance, state, guest["name"]))
        return 0

    path = "/nodes/%s/%s/%s/config" % (guest["node"], guest["type"], guest["vmid"])
    rc, _, err = pvesh(
        ["set", path, "-description", new_description, "-tags", new_tags, "--output-format", "json"]
    )
    if rc != 0:
        print("FAILED %s not recorded on %s: %s" % (args.instance, guest["name"], err.strip()))
        return 0

    verb = "withdrawn from" if args.withdraw else "recorded on"
    print("CHANGED %s %s %s (vmid %s)" % (args.instance, verb, guest["name"], guest["vmid"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
