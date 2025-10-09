from __future__ import annotations

import os
import secrets
from typing import Any, Dict, List, Optional
import json
import time
import sys
import threading

from flask import Flask, Response, jsonify, render_template, request, session, stream_with_context

# Load saved settings into environment before importing agent/config
SETTINGS_PATH = os.path.join(os.getcwd(), ".omni_agent", "settings.json")
try:
    if os.path.isfile(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            _sv = json.load(f) or {}
        if isinstance(_sv, dict):
            for _k, _v in _sv.items():
                if isinstance(_v, (str, int, float, bool)):
                    os.environ[str(_k)] = str(_v)
except Exception:
    pass

from omni_agent.agent.agent import Agent
from omni_agent.storage.conversations import ConversationStore
from omni_agent.tor.tor_client import DEFAULT_TOR
from omni_agent.config import CONFIG
from omni_agent.tools.fs_tools import search_files
from omni_agent.tools.local_files import load_file as tool_load_file
from omni_agent import context as AGCTX


app = Flask(__name__)
app.secret_key = os.environ.get("OMNI_FLASK_SECRET", secrets.token_hex(16))

# In-memory map of UI session -> Agent instance
AGENTS: Dict[str, Agent] = {}
STATE: Dict[str, Dict[str, Any]] = {}


def _ensure_uid() -> str:
    uid = session.get("uid")
    if not uid:
        uid = secrets.token_hex(12)
        session["uid"] = uid
    return uid


def get_agent() -> Agent:
    uid = _ensure_uid()
    if uid not in AGENTS:
        # Per-session state container
        STATE[uid] = {"busy": False, "current_tool": None, "last": 0.0, "stream": True}

        def observer(ev: Dict[str, Any]) -> None:
            s = STATE.get(uid)
            if not s:
                return
            et = ev.get("event")
            if et == "turn_start":
                s["busy"] = True
                s["current_tool"] = None
            elif et == "tool_start":
                s["current_tool"] = ev.get("name")
            elif et in {"tool_end", "tool_error"}:
                s["current_tool"] = None
            elif et == "turn_end":
                s["busy"] = False
                s["current_tool"] = None
            s["last"] = time.time()

        AGENTS[uid] = Agent(model=CONFIG.MODEL_NAME, observer=observer)
    return AGENTS[uid]


def current_session_id() -> str:
    ag = get_agent()
    return ag.session.id


@app.route("/")
def index():
    ag = get_agent()
    store = ag.store
    sid = ag.session.id
    msgs = store.load_messages(sid)
    sessions = store.list_sessions()
    tor_status = DEFAULT_TOR.status()
    return render_template(
        "gui/app.html",
        model=CONFIG.MODEL_NAME,
        sessions=sessions,
        current_sid=sid,
        messages=msgs,
        tor_status=tor_status,
    )


# Chat API
@app.post("/api/ask")
def api_ask():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"status": "error", "error": "empty input"}), 400
    ag = get_agent()
    ans = ag.ask(text) or ""
    return jsonify({"status": "ok", "answer": ans, "session_id": ag.session.id})


@app.get("/api/messages")
def api_messages():
    ag = get_agent()
    msgs = ag.store.load_messages(ag.session.id)
    # Convert to simple dicts for UI
    out = []
    for m in msgs:
        # hide empty assistant tool-call markers
        if getattr(m, "role", "") == "assistant" and (not getattr(m, "content", "")) and getattr(m, "tool_calls", None):
            continue
        out.append({
            "role": m.role,
            "content": m.content,
            "tool_name": m.tool_name,
            "tool_args": getattr(m, "tool_args", None),
            "created_at": getattr(m, "created_at", None),
        })
    return jsonify({"status": "ok", "messages": out, "session_id": ag.session.id})


@app.get("/api/ask_stream")
def api_ask_stream():
    text = request.args.get("q", "").strip()
    if not text:
        return jsonify({"status": "error", "error": "empty input"}), 400
    ag = get_agent()

    @stream_with_context
    def generate():
        try:
            for delta in ag.ask_stream(text):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


# Settings UI and control
@app.route("/settings")
def settings_page():
    # Populate settings from the currently loaded CONFIG (in-memory values)
    vals: Dict[str, str] = {
        "OMNI_MODEL": str(CONFIG.MODEL_NAME),
        "OMNI_UA": str(CONFIG.USER_AGENT),
        "OMNI_TIMEOUT": str(CONFIG.DEFAULT_TIMEOUT),
        "OMNI_VERIFY_SSL": "1" if CONFIG.VERIFY_SSL else "0",
        "OMNI_ENABLE_TOR": "1" if CONFIG.ENABLE_TOR else "0",
        "TOR_BIN": str(CONFIG.TOR_BIN or ""),
        "OMNI_TOR_LOG_LEVEL": str(CONFIG.TOR_LOG_LEVEL),
        "OMNI_TOR_SOCKS_PORT": str(CONFIG.TOR_SOCKS_PORT),
        "OMNI_TOR_CONTROL_PORT": str(CONFIG.TOR_CONTROL_PORT),
        "OMNI_NUM_CTX": str(CONFIG.NUM_CTX),
        "OMNI_NUM_PREDICT": str(CONFIG.NUM_PREDICT),
        "OMNI_CTX_MARGIN": str(CONFIG.CTX_MARGIN_TOKENS),
        "OMNI_MAX_TOOL_CTX_CHARS": str(CONFIG.MAX_TOOL_CONTEXT_CHARS),
    }
    return render_template("gui/settings.html", settings=vals)


@app.post("/api/settings")
def api_settings_save():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"status": "error", "error": "invalid payload"}), 400
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.post("/api/reload")
def api_reload():
    try:
        DEFAULT_TOR.stop()
    except Exception:
        pass

    def _restart():
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Timer(0.5, _restart).start()
    return jsonify({"status": "ok", "restarting": True})


@app.get("/api/home")
def api_home_get():
    ag = get_agent()
    return jsonify({"status": "ok", "home": ag.home_dir})


@app.post("/api/home")
def api_home_set():
    ag = get_agent()
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"status": "error", "error": "missing path"}), 400
    ag.set_home_dir(path)
    return jsonify({"status": "ok", "home": ag.home_dir})


@app.post("/api/search_files")
def api_search_files():
    ag = get_agent()
    payload = request.get_json(silent=True) or {}
    token = AGCTX.CURRENT_HOME.set(ag.home_dir)
    try:
        res = search_files(
            name=payload.get("name"),
            ext=payload.get("ext"),
            contains=payload.get("contains"),
            case_sensitive=bool(payload.get("case_sensitive")),
            regex=bool(payload.get("regex")),
            max_results=int(payload.get("max_results") or 50),
        )
    finally:
        AGCTX.CURRENT_HOME.reset(token)
    return jsonify(res)


@app.post("/api/open_file")
def api_open_file():
    ag = get_agent()
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"status": "error", "error": "missing path"}), 400
    token = AGCTX.CURRENT_HOME.set(ag.home_dir)
    try:
        res = tool_load_file(path)
    finally:
        AGCTX.CURRENT_HOME.reset(token)
    return jsonify(res)


# Sessions API
@app.post("/api/sessions/new")
def api_new_session():
    ag = get_agent()
    name = (request.get_json(silent=True) or {}).get("name")
    ag.new_session(name=name)
    return jsonify({"status": "ok", "session_id": ag.session.id})


@app.get("/api/sessions")
def api_list_sessions():
    ag = get_agent()
    sessions = ag.store.list_sessions()
    out = [{"id": s.id, "name": s.name, "updated_at": s.updated_at} for s in sessions]
    return jsonify({"status": "ok", "sessions": out, "current": ag.session.id})


@app.post("/api/sessions/load")
def api_load_session():
    sid = (request.get_json(silent=True) or {}).get("id")
    if not sid:
        return jsonify({"status": "error", "error": "missing id"}), 400
    ag = get_agent()
    ok = ag.load_session(sid)
    return jsonify({"status": "ok" if ok else "error", "loaded": ok, "session_id": ag.session.id})


@app.post("/api/sessions/rename")
def api_rename_session():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    ag = get_agent()
    if not name:
        return jsonify({"status": "error", "error": "missing name"}), 400
    ok = ag.store.rename(ag.session.id, name)
    return jsonify({"status": "ok" if ok else "error", "renamed": ok})


@app.post("/api/sessions/delete")
def api_delete_session():
    sid = (request.get_json(silent=True) or {}).get("id")
    if not sid:
        return jsonify({"status": "error", "error": "missing id"}), 400
    ag = get_agent()
    ok = ag.store.delete(sid)
    # If we deleted the current session, create a new one
    if ok and sid == ag.session.id:
        ag.new_session()
    return jsonify({"status": "ok" if ok else "error", "deleted": ok, "current": ag.session.id})


@app.get("/api/sessions/export/<sid>")
def api_export_session(sid: str):
    ag = get_agent()
    try:
        md = ag.store.export_markdown(sid)
    except FileNotFoundError:
        return jsonify({"status": "error", "error": "session not found"}), 404
    return jsonify({"status": "ok", "markdown": md})


# Tor API
@app.get("/api/tor/status")
def api_tor_status():
    st = DEFAULT_TOR.status()
    return jsonify({
        "status": "ok",
        "running": st.running,
        "socks_port": st.socks_port,
        "control_port": st.control_port,
        "binary": st.binary,
        "error": st.error,
    })


@app.post("/api/tor/on")
def api_tor_on():
    st = DEFAULT_TOR.start()
    return jsonify({"status": "ok" if st.running else "error", "running": st.running, "error": st.error})


@app.post("/api/tor/off")
def api_tor_off():
    DEFAULT_TOR.stop()
    return jsonify({"status": "ok"})


@app.get("/api/state")
def api_state():
    uid = _ensure_uid()
    st = STATE.get(uid) or {"busy": False, "current_tool": None, "last": 0.0, "stream": True}
    return jsonify({"status": "ok", **st})


@app.post("/api/prefs")
def api_prefs():
    uid = _ensure_uid()
    data = request.get_json(silent=True) or {}
    stream = data.get("stream")
    st = STATE.setdefault(uid, {"busy": False, "current_tool": None, "last": 0.0, "stream": True})
    if isinstance(stream, bool):
        st["stream"] = stream
    return jsonify({"status": "ok", "stream": st.get("stream", True)})


def main():
    port = int(os.environ.get("OMNI_GUI_PORT", "5000"))
    host = os.environ.get("OMNI_GUI_HOST", "127.0.0.1")
    debug = os.environ.get("FLASK_DEBUG", "0") in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
