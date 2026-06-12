from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentError(Exception):
    code: str
    message: str
    field: str | None = None

    def __str__(self) -> str:
        return self.message
