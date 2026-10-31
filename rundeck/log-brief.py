#!/usr/bin/env python3
"""Shrink a followed Rundeck log for a reader that pays per line, such as an LLM agent.

    rd executions follow -e <id> -f | python3 rundeck/log-brief.py

Reads stdin, writes stdout. It strips colour codes, drops every TASK banner that no result
line follows (the same rule LAB_LOG_BRIEF=1 applies at the source), and drops the repeated TLS warning. Failures and findings pass through untouched, and the Rundeck log itself is
never changed. LAB_LOG_BRIEF=1 on a direct run makes the same cut without this filter.
"""
import re
import sys

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BANNER = re.compile(r"^(TASK|RUNNING HANDLER|PLAY) \[")
# Two lines of Python's TLS warning, repeated once per Ansible process.
NOISE = re.compile(r"InsecureRequestWarning|^\s*warnings\.warn\(")


def brief(lines):
    pending = None
    for raw in lines:
        line = ANSI.sub("", raw.rstrip("\n"))
        if NOISE.search(line):
            continue
        if BANNER.match(line):
            if line.startswith("PLAY"):
                yield line
                pending = None
            else:
                pending = line
            continue
        if not line.strip():
            continue
        if pending is not None:
            yield pending
            pending = None
        yield line


if __name__ == "__main__":
    for out in brief(sys.stdin):
        print(out)
