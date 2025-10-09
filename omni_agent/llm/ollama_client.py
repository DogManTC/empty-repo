from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import os

from ollama import chat, show
try:
    from ollama._types import ResponseError as OllamaResponseError
except Exception:  # pragma: no cover
    OllamaResponseError = Exception  # type: ignore[assignment]

from omni_agent.config import CONFIG


def _get_field(obj: Any, *path: str, default=None):
    cur = obj
    for key in path:
        try:
            cur = getattr(cur, key)
        except Exception:
            try:
                cur = cur.get(key)
            except Exception:
                return default
    return cur if cur is not None else default


def _normalize_tool_calls(tool_calls_obj: Any) -> List[Dict[str, Any]]:
    """
    Normalize tool_calls to a list of dicts like:
    [{"function": {"name": "duck_search", "arguments": {...}}}, ...]
    """
    if not tool_calls_obj:
        return []
    out = []
    for tc in tool_calls_obj:
        try:
            fn = getattr(tc, "function", None) or tc.get("function", {})
            name = getattr(fn, "name", None) or fn.get("name")
            args = getattr(fn, "arguments", None) or fn.get("arguments") or {}
            out.append({"function": {"name": name, "arguments": args}})
        except Exception:
            out.append(tc)  # best-effort passthrough
    return out


JSON_FENCE_RE = __import__("re").compile(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", __import__("re").DOTALL)
JSON_BARE_RE = __import__("re").compile(r"^\s*(\[.*\]|\{.*\})\s*$", __import__("re").DOTALL)


def parse_inline_tool_calls(text: str) -> Optional[List[Dict[str, Any]]]:
    if not text or not text.strip():
        return None

    candidates: List[str] = []
    m = JSON_FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    else:
        if JSON_BARE_RE.match(text):
            candidates.append(text.strip())

    for blob in candidates:
        try:
            data = json.loads(blob)
        except Exception:
            continue

        def norm_one(x: Any) -> Optional[Dict[str, Any]]:
            if not isinstance(x, dict):
                return None
            name = x.get("name")
            args = x.get("arguments", {})
            if isinstance(name, str) and isinstance(args, dict):
                return {"function": {"name": name, "arguments": args}}
            f = x.get("function") if isinstance(x.get("function"), dict) else None
            if f and isinstance(f.get("name"), str):
                return {"function": {"name": f.get("name"), "arguments": f.get("arguments", {})}}
            return None

        if isinstance(data, dict):
            item = norm_one(data)
            if item:
                return [item]
        elif isinstance(data, list):
            out = [norm_one(x) for x in data]
            out = [x for x in out if x]
            if out:
                return out

    return None


class OllamaChat:
    def __init__(self, model: Optional[str] = None):
        self.model = model or CONFIG.MODEL_NAME
        self.supports_thinking = True
        self._model_max_ctx: Optional[int] = None

    def _probe_model_ctx(self) -> Optional[int]:
        try:
            info = show(self.model)
        except Exception:
            return None
        # Try common keys at top-level and nested dicts
        def find_num_ctx(d: Dict[str, Any]) -> Optional[int]:
            for key in ("num_ctx", "num_ctx_tokens", "ctx", "context_length", "context"):
                try:
                    val = d.get(key)
                    if isinstance(val, (int, float)):
                        return int(val)
                    if isinstance(val, str) and val.isdigit():
                        return int(val)
                except Exception:
                    continue
            return None

        if isinstance(info, dict):
            val = find_num_ctx(info)
            if val:
                return val
            for k in ("model_info", "details", "parameters", "options"):
                sub = info.get(k)
                if isinstance(sub, dict):
                    val = find_num_ctx(sub)
                    if val:
                        return val
        return None

    def get_effective_ctx(self) -> int:
        # Cache model max ctx
        if self._model_max_ctx is None:
            self._model_max_ctx = self._probe_model_ctx()
        model_max = self._model_max_ctx or CONFIG.NUM_CTX
        # If user explicitly set OMNI_NUM_CTX, respect it but don't exceed model max
        env_val = os.getenv("OMNI_NUM_CTX")
        if env_val:
            try:
                user = int(env_val)
                return max(512, min(user, model_max))
            except Exception:
                return model_max
        # Otherwise, use the model's max automatically
        return max(512, model_max)

    def chat_with_fallback(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None):
        kwargs = dict(
            model=self.model,
            messages=messages,
            tools=tools,
            options={
                "temperature": 0,
                "num_ctx": self.get_effective_ctx(),
                "num_predict": CONFIG.NUM_PREDICT,
            },
        )
        if self.supports_thinking:
            kwargs["think"] = True
        try:
            return chat(**kwargs)
        except OllamaResponseError as e:  # type: ignore[misc]
            msg = str(e).lower()
            if "does not support thinking" in msg or "thinking" in msg:
                self.supports_thinking = False
                kwargs.pop("think", None)
                return chat(**kwargs)
            raise

    def chat_stream(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None):
        """Yield streaming chunks from Ollama chat. Falls back if 'think' unsupported.

        Yields dict chunks from the ollama client which typically include partial 'message': {'content': '...'}.
        """
        kwargs = dict(
            model=self.model,
            messages=messages,
            tools=tools,
            options={
                "temperature": 0,
                "num_ctx": self.get_effective_ctx(),
                "num_predict": CONFIG.NUM_PREDICT,
            },
            stream=True,
        )
        if self.supports_thinking:
            kwargs["think"] = True
        try:
            for chunk in chat(**kwargs):
                yield chunk
        except OllamaResponseError as e:  # type: ignore[misc]
            msg = str(e).lower()
            if "does not support thinking" in msg or "thinking" in msg:
                self.supports_thinking = False
                kwargs.pop("think", None)
                for chunk in chat(**kwargs):
                    yield chunk
                return
            raise

    # Expose helpers for reuse
    get_field = staticmethod(_get_field)
    normalize_tool_calls = staticmethod(_normalize_tool_calls)
    parse_inline_tool_calls = staticmethod(parse_inline_tool_calls)
