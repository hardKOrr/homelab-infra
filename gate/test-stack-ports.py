#!/usr/bin/env python3
"""Applications that share a Docker stack publish distinct default host ports.

Every application with the same `stack` runs on one stack guest, so two defaults with the
same `app.port` cannot both start there: the second Deploy fails with "port is already
allocated".
"""

from collections import defaultdict
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]


def main():
    published = defaultdict(list)
    for path in sorted((repo / "ansible/vars/app-defaults").glob("*.yml")):
        for defaults in (yaml.safe_load(path.read_text()) or {}).values():
            if not isinstance(defaults, dict):
                continue
            stack = defaults.get("stack")
            port = (defaults.get("app") or {}).get("port")
            if stack and port:
                published[(stack, port)].append(path.stem)
    clashes = {key: apps for key, apps in published.items() if len(apps) > 1}
    assert not clashes, "applications on one stack share a default host port: " + "; ".join(
        f"stack {stack} port {port}: {', '.join(apps)}" for (stack, port), apps in sorted(clashes.items()))
    assert published, "no stack application defaults were read"
    print(f"stack ports: {len(published)} stack/port pairs, none shared")


if __name__ == "__main__":
    main()
