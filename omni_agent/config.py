"""
Central configuration for Omni Agent.
Edit values here to customize behavior.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


@dataclass(frozen=True)
class Config:
    # Model configuration
    MODEL_NAME: str = _env(
        "OMNI_MODEL",
        # Default to a community GGUF model reference as in the prototype
        "Omoeba/qwen3-2507-abliterated-max",
    )

    # HTTP behavior
    USER_AGENT: str = _env("OMNI_UA", "Mozilla/5.0 (compatible; OmniKeylessFetcher/1.0; +https://example.invalid)")
    DEFAULT_TIMEOUT: int = int(_env("OMNI_TIMEOUT", "45"))
    MAX_TOOL_CONTENT_CHARS: int = int(_env("OMNI_MAX_TOOL_CHARS", "90000"))
    VERIFY_SSL: bool = _env("OMNI_VERIFY_SSL", "true").lower() in {"1", "true", "yes", "on"}

    # Timezone
    DEFAULT_TIMEZONE: str = _env("OMNI_TZ", "America/New_York")

    # Tor
    ENABLE_TOR: bool = _env("OMNI_ENABLE_TOR", "true").lower() in {"1", "true", "yes", "on"}
    TOR_BIN: str = _env("TOR_BIN", "")  # optional absolute path to tor binary
    TOR_SOCKS_PORT: int = int(_env("OMNI_TOR_SOCKS_PORT", "0"))  # 0 => auto-pick
    TOR_CONTROL_PORT: int = int(_env("OMNI_TOR_CONTROL_PORT", "0"))  # 0 => auto-pick
    TOR_LOG_LEVEL: str = _env("OMNI_TOR_LOG_LEVEL", "notice")

    # Storage
    STORE_ROOT: str = _env("OMNI_STORE_ROOT", os.path.join(os.getcwd(), ".omni_agent"))

    # LLM / context management
    NUM_CTX: int = int(_env("OMNI_NUM_CTX", "262144"))  # desired context window tokens (capped by model)
    NUM_PREDICT: int = int(_env("OMNI_NUM_PREDICT", "512"))  # max tokens to generate
    CTX_MARGIN_TOKENS: int = int(_env("OMNI_CTX_MARGIN", "256"))  # safety buffer for tool schemas etc.
    CHARS_PER_TOKEN: float = float(_env("OMNI_CHARS_PER_TOKEN", "4.0"))  # rough heuristic
    MAX_TOOL_CONTEXT_CHARS: int = int(_env("OMNI_MAX_TOOL_CTX_CHARS", "20000"))  # per-tool msg cap in context


CONFIG = Config()
