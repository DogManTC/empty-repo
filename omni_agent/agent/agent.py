from __future__ import annotations

from typing import Any, Dict, List, Optional, Callable

from omni_agent.config import CONFIG
from omni_agent.llm.ollama_client import OllamaChat
from omni_agent.storage.conversations import ConversationStore, Message
from omni_agent.tools.clearnet import duck_search, fetch_url, DUCK_SEARCH_TOOL, FETCH_URL_TOOL
from omni_agent.tools.tor_tools import tor_search, tor_fetch, onion_up, TOR_SEARCH_TOOL, TOR_FETCH_TOOL, ONION_UP_TOOL
from omni_agent.tools.local_files import load_file, LOAD_FILE_TOOL
from omni_agent.tools.python_exec import python_exec, PYTHON_EXEC_TOOL
from omni_agent.tools.fs_tools import search_files, SEARCH_FILES_TOOL
from omni_agent import context
from omni_agent.utils.timeutils import build_system_prompt
from omni_agent.utils.text import to_str_preview


class Agent:
    def __init__(self, store: Optional[ConversationStore] = None, model: Optional[str] = None, observer: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.model = model or CONFIG.MODEL_NAME
        self.messages: List[Dict[str, Any]] = [{
            "role": "system",
            "content": build_system_prompt(),
        }]
        self.tools_payload = [DUCK_SEARCH_TOOL, FETCH_URL_TOOL, TOR_SEARCH_TOOL, TOR_FETCH_TOOL, ONION_UP_TOOL, LOAD_FILE_TOOL, PYTHON_EXEC_TOOL, SEARCH_FILES_TOOL]
        self.available_tools = {
            "duck_search": duck_search,
            "fetch_url": fetch_url,
            "tor_search": tor_search,
            "tor_fetch": tor_fetch,
            "onion_up": onion_up,
            "load_file": load_file,
            "python_exec": python_exec,
            "search_files": search_files,
        }
        self.ollama = OllamaChat(self.model)
        self.store = store or ConversationStore()
        self.session = self.store.new_session(model=self.model)
        self.observer = observer
        self.home_dir: Optional[str] = None

    def set_home_dir(self, path: Optional[str]) -> None:
        self.home_dir = path

    def _notify(self, event: str, **payload: Any) -> None:
        if self.observer:
            try:
                self.observer({"event": event, **payload})
            except Exception:
                pass

    def _approx_tokens(self, text: str) -> int:
        if not text:
            return 0
        return int(len(text) / max(1.0, CONFIG.CHARS_PER_TOKEN)) + 1

    def _clip_tool_content(self, content: str) -> str:
        if not content:
            return ""
        if len(content) > CONFIG.MAX_TOOL_CONTEXT_CHARS:
            return content[: CONFIG.MAX_TOOL_CONTEXT_CHARS]
        return content

    def _build_ctx_messages(self) -> List[Dict[str, Any]]:
        # Build a trimmed message list to fit within the configured context budget.
        # Ask the LLM client for effective ctx window and keep a margin.
        try:
            eff_ctx = self.ollama.get_effective_ctx()
        except Exception:
            eff_ctx = CONFIG.NUM_CTX
        budget = max(512, eff_ctx - CONFIG.CTX_MARGIN_TOKENS)
        system = self.messages[0] if self.messages and self.messages[0].get("role") == "system" else None
        rest = self.messages[1:] if system else self.messages[:]

        acc: List[Dict[str, Any]] = []
        total_tokens = 0
        # Walk from the end (most recent) backwards until budget
        for m in reversed(rest):
            m2 = dict(m)
            if m2.get("role") == "tool":
                # clip bulky tool content
                m2["content"] = self._clip_tool_content(m2.get("content") or "")
            # Rough token cost
            content = m2.get("content") or ""
            cost = self._approx_tokens(content) + 8  # small overhead per message
            if total_tokens + cost > budget and acc:
                break
            acc.append(m2)
            total_tokens += cost
        acc.reverse()
        if system:
            return [system] + acc
        return acc

    def _call_tools(self, tool_calls: List[Dict[str, Any]]) -> None:
        # Record the assistant action with tool_calls
        self.messages.append({"role": "assistant", "tool_calls": tool_calls})
        self.store.append(self.session.id, Message(role="assistant", content="", tool_calls=tool_calls))

        # Execute tools and append results
        for tc in tool_calls:
            fn_name = self.ollama.get_field(tc, "function", "name")
            fn_args = self.ollama.get_field(tc, "function", "arguments", default={}) or {}
            fn = self.available_tools.get(fn_name)

            if not fn:
                tool_msg = {
                    "role": "tool",
                    "tool_name": fn_name or "unknown",
                    "content": f"ERROR: Tool '{fn_name}' not available.",
                }
                self.messages.append(tool_msg)
                self.store.append(self.session.id, Message(role="tool", content=tool_msg["content"], tool_name=fn_name))
                continue

            if fn_name == "duck_search" and "max_results" not in fn_args:
                fn_args["max_results"] = 5

            try:
                self._notify("tool_start", name=fn_name, args=fn_args)
                token = context.CURRENT_HOME.set(self.home_dir)
                try:
                    result = fn(**fn_args)
                finally:
                    context.CURRENT_HOME.reset(token)
                preview = to_str_preview(result, max_chars=12000)
                self.messages.append({"role": "tool", "tool_name": fn_name, "content": preview, "tool_args": fn_args})
                self.store.append(self.session.id, Message(role="tool", content=preview, tool_name=fn_name, tool_args=fn_args))
                self._notify("tool_end", name=fn_name)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                self.messages.append({"role": "tool", "tool_name": fn_name, "content": f"ERROR: {err}", "tool_args": fn_args})
                self.store.append(self.session.id, Message(role="tool", content=f"ERROR: {err}", tool_name=fn_name, tool_args=fn_args))
                self._notify("tool_error", name=fn_name, error=str(e))

    def ask(self, user_text: str) -> Optional[str]:
        self._notify("turn_start")
        self.messages.append({"role": "user", "content": user_text})
        self.store.append(self.session.id, Message(role="user", content=user_text))

        while True:
            ctx_msgs = self._build_ctx_messages()
            response = self.ollama.chat_with_fallback(ctx_msgs, self.tools_payload)

            content = self.ollama.get_field(response, "message", "content", default="") or ""
            raw_tool_calls = self.ollama.get_field(response, "message", "tool_calls")
            tool_calls = self.ollama.normalize_tool_calls(raw_tool_calls)

            if not tool_calls and content:
                inline_calls = self.ollama.parse_inline_tool_calls(content)
                if inline_calls:
                    tool_calls = inline_calls
                    content = ""

            if content.strip():
                self.messages.append({"role": "assistant", "content": content})
                self.store.append(self.session.id, Message(role="assistant", content=content))
                self._notify("assistant_message", content=content)

            if tool_calls:
                self._call_tools(tool_calls)
                continue

            # If we reached here with no content and no tool calls, the model likely emitted EOS.
            # Force a follow-up by injecting a minimal user nudge and prefixing the reply with "\nSo, ".
            if not content.strip():
                nudge = "Summarize the results succinctly."
                self.messages.append({"role": "user", "content": nudge})
                self.store.append(self.session.id, Message(role="user", content=nudge))
                ctx_msgs2 = self._build_ctx_messages()
                response2 = self.ollama.chat_with_fallback(ctx_msgs2, self.tools_payload)
                content2 = self.ollama.get_field(response2, "message", "content", default="") or ""
                if content2.strip():
                    forced = "\nSo, " + content2
                    self.messages.append({"role": "assistant", "content": forced})
                    self.store.append(self.session.id, Message(role="assistant", content=forced))
                    self._notify("assistant_message", content=forced)
                    self._notify("turn_end")
                    return forced

            self._notify("turn_end")
            return content or None

    def ask_stream(self, user_text: str):
        """Generator that yields assistant text deltas as they arrive.

        Tool execution still happens synchronously; streaming applies to the final assistant response.
        """
        self._notify("turn_start")
        self.messages.append({"role": "user", "content": user_text})
        self.store.append(self.session.id, Message(role="user", content=user_text))

        # First pass: see if tools are requested
        ctx_msgs = self._build_ctx_messages()
        response = self.ollama.chat_with_fallback(ctx_msgs, self.tools_payload)
        content = self.ollama.get_field(response, "message", "content", default="") or ""
        raw_tool_calls = self.ollama.get_field(response, "message", "tool_calls")
        tool_calls = self.ollama.normalize_tool_calls(raw_tool_calls)

        if content.strip():
            # If the model already returned content without tools, we can stream nothing and just return it.
            self.messages.append({"role": "assistant", "content": content})
            self.store.append(self.session.id, Message(role="assistant", content=content))
            self._notify("assistant_message", content=content)
            self._notify("turn_end")
            yield content
            return

        if tool_calls:
            self._call_tools(tool_calls)

        # Second pass: produce final answer via streaming
        final_text: List[str] = []
        ctx_msgs2 = self._build_ctx_messages()
        for chunk in self.ollama.chat_stream(ctx_msgs2, self.tools_payload):
            delta = self.ollama.get_field(chunk, "message", "content", default="") or ""
            if not delta:
                continue
            final_text.append(delta)
            self._notify("assistant_delta", delta=delta)
            yield delta

        full = "".join(final_text)
        if full:
            self.messages.append({"role": "assistant", "content": full})
            self.store.append(self.session.id, Message(role="assistant", content=full))
            self._notify("turn_end")
            return

        # No deltas produced — force continuation with a nudge and a visible prefix.
        nudge = "Summarize the results succinctly."
        self.messages.append({"role": "user", "content": nudge})
        self.store.append(self.session.id, Message(role="user", content=nudge))
        prefix = "\nSo, "
        self._notify("assistant_delta", delta=prefix)
        yield prefix
        final_text2: List[str] = []
        ctx_msgs3 = self._build_ctx_messages()
        for chunk in self.ollama.chat_stream(ctx_msgs3, self.tools_payload):
            delta = self.ollama.get_field(chunk, "message", "content", default="") or ""
            if not delta:
                continue
            final_text2.append(delta)
            self._notify("assistant_delta", delta=delta)
            yield delta
        full2 = "".join(final_text2)
        if full2:
            combined = prefix + full2
            self.messages.append({"role": "assistant", "content": combined})
            self.store.append(self.session.id, Message(role="assistant", content=combined))
        self._notify("turn_end")
        return

    # Session control helpers
    def new_session(self, name: Optional[str] = None) -> None:
        self.session = self.store.new_session(name=name, model=self.model)
        self.messages = [{"role": "system", "content": build_system_prompt()}]

    def load_session(self, sid: str) -> bool:
        try:
            msgs = self.store.load_messages(sid)
        except FileNotFoundError:
            return False
        self.session = next((m for m in self.store.list_sessions() if m.id == sid), self.store.new_session(model=self.model))
        # rebuild messages
        self.messages = [{"role": "system", "content": build_system_prompt()}]
        for m in msgs:
            if m.role == "tool":
                self.messages.append({"role": "tool", "tool_name": m.tool_name or "", "content": m.content})
            elif m.role == "assistant" and m.tool_calls:
                self.messages.append({"role": "assistant", "tool_calls": m.tool_calls})
            else:
                self.messages.append({"role": m.role, "content": m.content})
        return True
