"""Stable Isotope errors and process exit codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_MALFORMED = 4
EXIT_AMBIGUOUS = 5
EXIT_CONFLICT = 6
EXIT_REFUSED = 7
EXIT_INTERNAL = 8


@dataclass
class IsotopeError(Exception):
    code: str
    message: str
    exit_code: int
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message
