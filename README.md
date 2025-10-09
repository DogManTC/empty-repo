Omni Agent
==========

A supercharged, modular Python agent featuring:

- Integrated headless Tor client (via stem) — no external Tor API server required
- Clearnet tools: DuckDuckGo search and robust URL fetching with PDF support
- Tor tools: page fetch via Tor and basic search via Ahmia/searx-compatible endpoints
- Local tools: load local files (.txt/.md/.html/.pdf) for analysis
- Utilities: check .onion availability; sandboxed Python execution tool
- Stateful conversation agent with Ollama tool-calling integration
- Conversation saving, listing, loading, deleting, and exporting
- Cross-module, clean architecture for easy extension

Quick start
----------

1. Prerequisites
   - Python 3.10+
   - Ollama installed and running, with a local model pulled (e.g., run: ollama pull mistral:7b). You can change the model via OMNI_MODEL.
   - Optional: Tor binary installed. On Windows, the agent tries to discover tor.exe automatically; you can also set TOR_BIN to the full path.

2. Create a virtual environment and install dependencies
   Option A — requirements.txt (recommended for quick run):
   - Windows (PowerShell):
     python -m venv .venv; .\.venv\Scripts\Activate.ps1; python -m pip install -r requirements.txt
   - Windows (CMD):
     python -m venv .venv && .\.venv\Scripts\activate && python -m pip install -r requirements.txt
   - Unix/macOS:
     python3 -m venv .venv && source .venv/bin/activate && python -m pip install -r requirements.txt

   Option B — editable install (for development):
   - After activating your venv, run:
     pip install -e .

3. Run it
   - Interactive REPL (stateful), simplest:
     python client.py
   - Alternative module entry:
     python -m omni_agent.cli
   - One‑shot prompt (prints a single answer and exits):
     python client.py Your question here

4. Inside the REPL, use commands:
   - /new — start a new conversation session
   - /list — list saved sessions
   - /load <session_id> — load a session by id
   - /delete <session_id> — delete a session by id
   - /saveas <name> — rename current session
   - /export <session_id> — export a session to Markdown
   - /tor [on|off|status] — manage the embedded Tor client

Web UI
------

- Start the server with: python GUI_client.py
- Default bind: 127.0.0.1:5000 (override with OMNI_GUI_HOST/OMNI_GUI_PORT)
- Features:
  - Chat with the agent (same tools and state as the CLI)
  - Manage sessions (new, rename, switch, delete, export Markdown)
  - Tor controls (start/stop, status)

Configuration
-------------

- Edit omni_agent/config.py for defaults, or override via environment variables.
- Common environment variables:
  - OMNI_MODEL: Ollama model tag to use (default set in config; you can set e.g., OMNI_MODEL=mistral:7b)
  - OMNI_UA: Custom HTTP User-Agent string
  - OMNI_TZ: Default timezone (e.g., America/New_York)
  - OMNI_TIMEOUT: Default HTTP timeout in seconds (e.g., 45)
  - OMNI_MAX_TOOL_CHARS: Cap on tool output characters (e.g., 90000)
  - OMNI_VERIFY_SSL: 1/true/yes to verify TLS certificates (default true)
  - OMNI_STORE_ROOT: Root folder for conversation storage (default ./.omni_agent)
  - OMNI_ENABLE_TOR: 1/true/yes to enable embedded Tor (default true)
  - TOR_BIN: Absolute path to the tor binary (if auto-discovery fails)
  - OMNI_TOR_SOCKS_PORT: SOCKS port (0 = auto)
  - OMNI_TOR_CONTROL_PORT: Control port (0 = auto)
  - OMNI_TOR_LOG_LEVEL: Tor log level (e.g., notice, info)

Tor
---

Tor is launched headlessly using stem. By default the agent will:
- Look for tor.exe (Windows) or tor (POSIX) in common locations or in PATH.
- On Windows, it also checks typical Tor Browser installs, for example:
  - %LOCALAPPDATA%\Tor Browser\Browser\TorBrowser\Tor\tor.exe
  - %ProgramFiles%\Tor Browser\Browser\TorBrowser\Tor\tor.exe
  - %ProgramFiles(x86)%\Tor Browser\Browser\TorBrowser\Tor\tor.exe
  - %USERPROFILE%\Desktop\Tor Browser\Browser\TorBrowser\Tor\tor.exe
  - %OneDrive%\Desktop\Tor Browser\Browser\TorBrowser\Tor\tor.exe (OneDrive Desktop redirection)
  - C:\Tor\tor.exe, Chocolatey (C:\ProgramData\chocolatey\bin\tor.exe) and Scoop (%USERPROFILE%\scoop\apps\tor\current\bin\tor.exe)
- Create a temporary DataDirectory for Tor runtime state.
- Bind to an available local SOCKS port.

If Tor cannot be launched, the agent will operate without Tor and clearly report this status. You can set TOR_BIN to the full path of your tor executable to override autodiscovery.

Available tools
---------------

- duck_search: search the web using DuckDuckGo (keyless)
- fetch_url: fetch any clearnet URL and extract content/links
- tor_search: search via Tor (Ahmia/searx-compatible endpoints)
- tor_fetch: fetch a page via the local Tor client
- onion_up: return true/false if a .onion host/URL is reachable via Tor
- load_file: load a local .txt/.md/.html/.pdf and return text + metadata
- python_exec: run small Python snippets in a sandboxed subprocess (imports off by default)

Storage
-------

- Conversations are stored under ./.omni_agent/sessions as JSONL plus metadata.
- Use the REPL commands (/list, /load, /saveas, /export, /delete) to manage them.

Troubleshooting
---------------

- Ollama model not found: Ensure Ollama is running and that the model you set in OMNI_MODEL is available locally (use: ollama pull <model>). The agent automatically falls back if your model does not support "thinking" mode.
- Tor not starting: Set TOR_BIN to your tor executable or disable Tor with OMNI_ENABLE_TOR=0. You can check status with /tor status in the REPL.
- Windows Tor note: the Tor launcher omits timeout on Windows due to a Stem limitation ("You cannot launch tor with a timeout on Windows").

Limitations
-----------

- Some functions require external binaries or services (Tor, Ollama) which must be present on your system.
- PDF extraction requires pdfminer.six.
- The included Tor search uses Ahmia/searx-compatible endpoints; results depend on availability.

License
-------

This repository is provided as-is for demonstration and development purposes.
