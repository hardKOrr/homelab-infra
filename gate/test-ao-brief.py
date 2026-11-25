#!/usr/bin/env python3
"""tools/ao-brief: the brief it renders from fixture GitHub data, without calling GitHub."""
import importlib.machinery
import importlib.util
import os
import sys

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader("ao_brief", os.path.join(repo, "tools", "ao-brief"))
spec = importlib.util.spec_from_loader("ao_brief", loader)
brief = importlib.util.module_from_spec(spec)
loader.exec_module(brief)
failures = []


def check(name, condition, detail=""):
    if not condition:
        failures.append(f"{name}: {detail[:400]}")


HEAD = "2eaab2d0875c4486ad40c12c935d61e7b504c09a"
handoff = f"### Handoff\n\nSTATUS: handoff\nHEAD: {HEAD[:8]}\n\nNext: drill.\n<!-- ao-event session=x-1 kind=handoff reason=planned -->"
issue = {
    "number": 606, "title": "Jellyfin runs on Kubernetes", "state": "OPEN", "url": "u",
    "subIssues": {"nodes": [{"number": 961, "title": "Code", "state": "CLOSED"},
                            {"number": 962, "title": "Drill", "state": "OPEN"}]},
    "comments": {"nodes": [{"body": "plain comment", "createdAt": "2026-11-25T10:00:00Z", "url": "c0"},
                           {"body": handoff, "createdAt": "2026-11-25T11:00:00Z", "url": "c1"},
                           {"body": "later chat", "createdAt": "2026-11-25T12:00:00Z", "url": "c2"}]},
}
pr = {
    "number": 994, "state": "OPEN", "url": "p", "headRefName": "ao/x", "headRefOid": HEAD,
    "isDraft": False, "body": "x" * 5000,
    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE", "contexts": {"nodes": [
        {"name": "gate", "conclusion": "FAILURE", "status": "COMPLETED"},
        {"name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED"}]}}}}]},
    "reviewThreads": {"nodes": [
        {"isResolved": False, "path": "roles/a.yml", "line": 12,
         "comments": {"nodes": [{"author": {"login": "rev"}, "body": "fix this"}]}},
        {"isResolved": True, "path": "roles/b.yml", "line": 3,
         "comments": {"nodes": [{"author": {"login": "rev"}, "body": "done already"}]}}]},
}
local = {"branch": "ao/x", "head": HEAD, "dirty": False}
pr["logs"] = {"gate": "step one\nAssertionError: boom"}

out = brief.render(issue, pr, local)
check("only open tasks are listed", "#962 Drill" in out and "#961" not in out and "1 open of 2" in out, out)
check("the latest handoff is shown without its marker", "Latest handoff comment" in out
      and "Next: drill." in out and "ao-event session" not in out and "later chat" not in out, out)
check("only failing checks are listed", "- gate: failure" in out and "lint" not in out, out)
check("a failing check shows its log tail", "Last 40 lines" in out and "AssertionError: boom" in out, out)
check("only unresolved threads are listed", "rev roles/a.yml:12: fix this" in out and "done already" not in out, out)
check("a long PR body is trimmed with a note", "[... trimmed 2000 characters" in out, out)
check("PR and handoff HEAD match local", "PR head: matches" in out and f"handoff HEAD {HEAD[:8]}: matches" in out, out)

pr["logs"] = {"gate": None}
out = brief.render(issue, pr, local)
check("an unreadable log says how to get it", "could not be read" in out, out)
out = brief.render({**issue, "comments": {"nodes": []}}, None, {**local, "head": "0" * 40, "dirty": True})
check("missing parts are said plainly", "- none yet" in out and "no PR yet" in out and "uncommitted changes" in out, out)
out = brief.render({**issue, "comments": {"nodes": [{"body": handoff, "createdAt": "2026-11-25T11:00:00Z", "url": "c"}]}},
                   None, {**local, "head": "f" * 40})
check("a HEAD mismatch is reported", "differs from local HEAD" in out, out)
check("ao-links are parsed", brief.linked_issues("<!-- ao-links -->\nCloses #961 · Closes #962\nCloses #606\n<!-- /ao-links -->")
      == [961, 962, 606])

raw = "\n".join(f"gate\tUNKNOWN STEP\t2026-11-25T20:10:09.9Z {line}" for line in
                ["##[group]Run gate", "== ok   gate/test-a.py", "== FAIL gate/test-b.py", "why it failed",
                 "##[error]Process completed with exit code 1.", "Post job cleanup.", "git version"])
brief.sh = lambda *args: raw
log = brief.failed_log(1)
check("a failed log keeps the failure and ends at the error", log ==
      "== FAIL gate/test-b.py\nwhy it failed\n##[error]Process completed with exit code 1.", log or "")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ao-brief: ok")
