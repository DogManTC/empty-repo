from __future__ import annotations

import sys
from typing import Optional

import os
from omni_agent.agent.agent import Agent
from omni_agent.storage.conversations import ConversationStore
from omni_agent.tor.tor_client import DEFAULT_TOR
from omni_agent.config import CONFIG


def _print_help() -> None:
    print("Commands:")
    print("  /new [name]            Start a new session")
    print("  /list                  List sessions")
    print("  /load <id>             Load session by id")
    print("  /delete <id>           Delete session")
    print("  /saveas <name>         Rename current session")
    print("  /export <id>           Export session to Markdown (prints to stdout)")
    print("  /tor [on|off|status]   Control the embedded Tor client")
    print("  /home-dir <path>       Set the accessible home directory for file tools")
    print("  /help                  Show this help")
    print("  Ctrl+C                 Exit")


def _tor_cmd(arg: Optional[str]) -> None:
    if not arg or arg == "status":
        st = DEFAULT_TOR.status()
        if st.running:
            print(f"Tor is running on SOCKS {st.socks_port}, Control {st.control_port} (bin={st.binary})")
        else:
            print(f"Tor is stopped. Last error: {st.error or 'n/a'}")
        return
    if arg == "on":
        st = DEFAULT_TOR.start()
        if st.running:
            print(f"Tor started: SOCKS {st.socks_port}, Control {st.control_port}")
        else:
            print(f"Failed to start Tor: {st.error}")
        return
    if arg == "off":
        DEFAULT_TOR.stop()
        print("Tor stopped.")
        return
    print("Unknown /tor argument. Use on|off|status")


def repl() -> None:
    store = ConversationStore()
    stream = os.environ.get("OMNI_STREAM", "0").lower() in {"1", "true", "yes", "on"}

    def observer(ev):
        if ev.get("event") == "assistant_delta" and stream:
            # Print deltas as they arrive (no extra newline)
            print(ev.get("delta", ""), end="", flush=True)
    agent = Agent(store=store, model=CONFIG.MODEL_NAME, observer=observer if stream else None)

    print(f"{CONFIG.MODEL_NAME} + DuckDuckGo + Local Tor (stateful). Type /help for commands. Ctrl+C to exit.")

    while True:
        try:
            q = input("\nYou: ").strip()
            if not q:
                continue
            if q.startswith("/"):
                parts = q.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else None
                if cmd == "/help":
                    _print_help()
                elif cmd == "/new":
                    agent.new_session(name=arg)
                    print("Started a new session.")
                elif cmd == "/list":
                    sessions = store.list_sessions()
                    if not sessions:
                        print("No sessions.")
                    for m in sessions:
                        print(f"- {m.id}  {m.name}  updated={m.updated_at}")
                elif cmd == "/load" and arg:
                    if agent.load_session(arg):
                        print(f"Loaded session {arg}")
                    else:
                        print(f"Session {arg} not found")
                elif cmd == "/delete" and arg:
                    ok = store.delete(arg)
                    print("Deleted." if ok else "Delete failed or session not found.")
                elif cmd == "/saveas" and arg:
                    ok = store.rename(agent.session.id, arg)
                    print("Renamed." if ok else "Rename failed.")
                elif cmd == "/export" and arg:
                    print(store.export_markdown(arg))
                elif cmd == "/tor":
                    _tor_cmd(arg)
                elif cmd == "/home-dir" and arg:
                    agent.set_home_dir(arg)
                    print(f"Home directory set to: {arg}")
                else:
                    print("Unknown command or missing argument. /help for help.")
                continue

            # normal turn
            if stream:
                printed = False
                for _ in agent.ask_stream(q):
                    printed = True
                if not printed:
                    # If nothing streamed (e.g., tool-only step), print last assistant message
                    st = store.load_messages(agent.session.id)
                    if st and st[-1].role == "assistant":
                        print(st[-1].content)
                    else:
                        print()
                else:
                    print()
            else:
                ans = agent.ask(q)
                if ans:
                    print(ans)
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # one-shot query mode
        store = ConversationStore()
        stream = os.environ.get("OMNI_STREAM", "0").lower() in {"1", "true", "yes", "on"}
        def observer(ev):
            if ev.get("event") == "assistant_delta" and stream:
                print(ev.get("delta", ""), end="", flush=True)
        agent = Agent(store=store, model=CONFIG.MODEL_NAME, observer=observer if stream else None)
        q = " ".join(sys.argv[1:]).strip()
        if stream:
            any_delta = False
            for _ in agent.ask_stream(q):
                any_delta = True
            if not any_delta:
                st = store.load_messages(agent.session.id)
                if st and st[-1].role == "assistant":
                    print(st[-1].content)
            else:
                print()
        else:
            print(agent.ask(q) or "")
    else:
        repl()
