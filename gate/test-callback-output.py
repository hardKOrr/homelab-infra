#!/usr/bin/env python3
"""Focused regression tests for the homelab stdout callback."""

from __future__ import annotations

import importlib.util
from pathlib import Path


class RecordingDisplay:
    columns = 80
    verbosity = 0

    def __init__(self) -> None:
        self.messages: list[str] = []

    def display(self, message: str, **_kwargs: object) -> None:
        self.messages.append(message)


class Task:
    _uuid = "compact-task"
    args: dict[str, str] = {}
    check_mode = False
    no_log = False

    @staticmethod
    def get_name() -> str:
        return "Confirm compact output"


repo = Path(__file__).resolve().parents[1]
module_path = repo / "ansible" / "callback_plugins" / "homelab.py"
spec = importlib.util.spec_from_file_location("homelab_callback", module_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

callback = object.__new__(module.CallbackModule)
callback._display = RecordingDisplay()
callback._task_type_cache = {}
callback._last_task_name = None
callback._last_task_banner = None
callback._print_task_banner(Task())

assert len(callback._display.messages) == 1
banner = callback._display.messages[0]
assert banner.startswith("TASK [Confirm compact output] ")
assert "\n" not in banner
assert len(banner) == callback._display.columns + 1

print("Callback output focused tests passed.")
