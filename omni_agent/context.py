from __future__ import annotations

from contextvars import ContextVar
from typing import Optional


# Per-request/turn context for tools
CURRENT_HOME: ContextVar[Optional[str]] = ContextVar("CURRENT_HOME", default=None)


def get_home_dir() -> Optional[str]:
    return CURRENT_HOME.get()

