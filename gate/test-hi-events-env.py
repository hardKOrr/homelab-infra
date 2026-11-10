#!/usr/bin/env python3
"""Render Hi.Events' Docker env file and read it back as Compose's env_file does."""

import re
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
VALUES = {
    "MAIL_FROM_NAME": "O'Brien Events",
    "MAIL_PASSWORD": 'p\'a"s\\s$HOME ${X} $$y',
    "NOTE": "line1\nline2",
    "PLAIN": "abc=def+/",
}


def parse(text):
    """Double-quoted dotenv values: \\\\ \\" \\$ \\n escapes, everything else literal."""
    out = {}
    for line in text.splitlines():
        match = re.fullmatch(r'([A-Z_]+)="((?:[^"\\]|\\.)*)"', line)
        if not match:
            continue
        out[match[1]] = re.sub(
            r"\\(.)", lambda m: "\n" if m[1] == "n" else m[1], match[2]
        )
    return out


def main():
    init_plugin_loader()
    template = (ROOT / "ansible/roles/hi-events/templates/hi-events.env.j2").read_text()
    rendered = Templar(loader=DataLoader(), variables={"_he_env": VALUES, "ansible_managed": "m"}).template(template)
    assert parse(rendered) == VALUES, rendered
    print("Hi.Events env file round-trips apostrophes, quotes, backslashes, dollars and newlines")


if __name__ == "__main__":
    main()
