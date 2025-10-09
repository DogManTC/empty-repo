from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from omni_agent.config import CONFIG


@dataclass
class Message:
    role: str
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None


@dataclass
class SessionMeta:
    id: str
    name: str
    created_at: str
    updated_at: str
    model: str


class ConversationStore:
    def __init__(self, root: Optional[str] = None):
        self.root = root or CONFIG.STORE_ROOT
        self.sessions_dir = os.path.join(self.root, "sessions")
        self._lock = threading.Lock()
        os.makedirs(self.sessions_dir, exist_ok=True)

    def _session_path(self, sid: str) -> str:
        return os.path.join(self.sessions_dir, f"{sid}.jsonl")

    def _meta_path(self, sid: str) -> str:
        return os.path.join(self.sessions_dir, f"{sid}.meta.json")

    def new_session(self, name: Optional[str] = None, model: Optional[str] = None) -> SessionMeta:
        sid = uuid.uuid4().hex[:12]
        now = datetime.utcnow().isoformat() + "Z"
        meta = SessionMeta(
            id=sid,
            name=name or f"session-{sid}",
            created_at=now,
            updated_at=now,
            model=model or "unknown",
        )
        with self._lock:
            with open(self._meta_path(sid), "w", encoding="utf-8") as f:
                json.dump(asdict(meta), f, ensure_ascii=False, indent=2)
            # create empty jsonl file
            open(self._session_path(sid), "a", encoding="utf-8").close()
        return meta

    def append(self, sid: str, msg: Message) -> None:
        rec = {k: v for k, v in asdict(msg).items() if v is not None}
        with self._lock:
            with open(self._session_path(sid), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            # touch meta updated_at
            try:
                with open(self._meta_path(sid), "r", encoding="utf-8") as f:
                    meta = json.load(f)
                meta["updated_at"] = datetime.utcnow().isoformat() + "Z"
                with open(self._meta_path(sid), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    def list_sessions(self) -> List[SessionMeta]:
        out: List[SessionMeta] = []
        for name in sorted(os.listdir(self.sessions_dir)):
            if not name.endswith(".meta.json"):
                continue
            sid = name[:-10]  # strip .meta.json
            try:
                with open(os.path.join(self.sessions_dir, name), "r", encoding="utf-8") as f:
                    meta = json.load(f)
                out.append(SessionMeta(**meta))
            except Exception:
                continue
        # sort by updated desc
        out.sort(key=lambda m: m.updated_at, reverse=True)
        return out

    def load_messages(self, sid: str) -> List[Message]:
        msgs: List[Message] = []
        with open(self._session_path(sid), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    msgs.append(Message(**obj))
                except Exception:
                    continue
        return msgs

    def delete(self, sid: str) -> bool:
        ok = True
        try:
            os.remove(self._session_path(sid))
        except Exception:
            ok = False
        try:
            os.remove(self._meta_path(sid))
        except Exception:
            ok = False
        return ok

    def rename(self, sid: str, new_name: str) -> bool:
        try:
            with open(self._meta_path(sid), "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["name"] = new_name
            meta["updated_at"] = datetime.utcnow().isoformat() + "Z"
            with open(self._meta_path(sid), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def export_markdown(self, sid: str) -> str:
        lines = []
        meta_path = self._meta_path(sid)
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            lines.append(f"# Conversation {meta.get('name')} ({sid})\n")
        except Exception:
            lines.append(f"# Conversation {sid}\n")
        for m in self.load_messages(sid):
            role = m.role
            lines.append(f"\n## {role.capitalize()}\n")
            if m.content:
                lines.append(m.content)
            if m.tool_calls:
                lines.append("\n<details><summary>Tool Calls</summary>\n\n")
                lines.append("```json")
                lines.append(json.dumps(m.tool_calls, ensure_ascii=False, indent=2))
                lines.append("```\n\n</details>")
        return "\n".join(lines)
