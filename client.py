from __future__ import annotations

import os
import sys

from omni_agent.cli.main import repl
from omni_agent.agent.agent import Agent
from omni_agent.storage.conversations import ConversationStore
from omni_agent.config import CONFIG


def main() -> None:
    if len(sys.argv) > 1:
        # One-shot query mode: print the assistant's reply and exit
        store = ConversationStore()
        stream = os.environ.get("OMNI_STREAM", "0").lower() in {"1", "true", "yes", "on"}
        def observer(ev):
            if ev.get("event") == "assistant_delta" and stream:
                print(ev.get("delta", ""), end="", flush=True)
        agent = Agent(store=store, model=CONFIG.MODEL_NAME, observer=observer if stream else None)
        query = " ".join(sys.argv[1:]).strip()
        if query:
            if stream:
                any_delta = False
                for _ in agent.ask_stream(query):
                    any_delta = True
                if any_delta:
                    print()
                else:
                    print(agent.ask("") or "")
            else:
                print(agent.ask(query) or "")
        return

    # Interactive REPL mode (stateful)
    repl()


if __name__ == "__main__":
    main()
