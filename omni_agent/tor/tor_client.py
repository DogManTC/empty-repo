from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, Optional

import requests
from stem.process import launch_tor_with_config  # type: ignore

from omni_agent.config import CONFIG


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    addr, port = s.getsockname()
    s.close()
    return int(port)


def _is_windows() -> bool:
    return os.name == "nt"


def _candidate_tor_paths() -> list[str]:
    # Respect environment variable override first
    if CONFIG.TOR_BIN:
        return [CONFIG.TOR_BIN]

    candidates: list[str] = []

    exe_name = "tor.exe" if _is_windows() else "tor"

    # PATH (check both tor and tor.exe on Windows)
    for name in {exe_name, "tor"}:
        p = shutil.which(name)
        if p:
            candidates.append(p)

    # Local project dir (e.g., ./tor/tor.exe)
    candidates.append(os.path.join(os.getcwd(), "tor", exe_name if _is_windows() else "tor"))

    if _is_windows():
        program_files = os.environ.get("ProgramFiles", r"C:\\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("LocalAppData")
        user_profile = os.environ.get("USERPROFILE")

        # Tor Expert Bundle conventional installs
        candidates.extend([
            os.path.join(program_files, "Tor", "tor.exe"),
            os.path.join(program_files_x86, "Tor", "tor.exe"),
            r"C:\\Tor\\tor.exe",
        ])

        # Tor Browser typical locations
        def tb_path(root: Optional[str]) -> Optional[str]:
            if not root:
                return None
            return os.path.join(root, "Tor Browser", "Browser", "TorBrowser", "Tor", "tor.exe")

        for root in [program_files, program_files_x86, local_app_data, (os.path.join(user_profile, "AppData", "Local", "Programs") if user_profile else None)]:
            pth = tb_path(root)
            if pth:
                candidates.append(pth)

        # Desktop portable Tor Browser (including OneDrive Desktop redirection)
        if user_profile:
            # Standard Desktop path
            candidates.append(os.path.join(user_profile, "Desktop", "Tor Browser", "Browser", "TorBrowser", "Tor", "tor.exe"))

            # OneDrive Desktop redirection (common on Windows)
            one_drive_root = os.environ.get("OneDrive") or os.path.join(user_profile, "OneDrive")
            if one_drive_root:
                candidates.append(os.path.join(one_drive_root, "Desktop", "Tor Browser", "Browser", "TorBrowser", "Tor", "tor.exe"))

            # Scoop installs
            candidates.append(os.path.join(user_profile, "scoop", "apps", "tor", "current", "bin", "tor.exe"))

        # Chocolatey installs
        candidates.extend([
            r"C:\\ProgramData\\chocolatey\\bin\\tor.exe",
            r"C:\\ProgramData\\chocolatey\\lib\\tor\\tools\\tor.exe",
        ])
    else:
        # Common POSIX paths (in addition to PATH)
        candidates.extend([
            "/usr/bin/tor",
            "/usr/local/bin/tor",
            "/opt/homebrew/bin/tor",
            "/opt/local/bin/tor",
        ])

    # De-duplicate while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for p in candidates:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


@dataclass
class TorStatus:
    running: bool
    socks_port: Optional[int]
    control_port: Optional[int]
    binary: Optional[str]
    data_dir: Optional[str]
    error: Optional[str] = None


class TorManager:
    """
    Manage a headless Tor subprocess and provide requests proxy dicts.
    If Tor cannot be started, exposes a clear status and does not crash the app.
    """

    def __init__(self):
        self._proc = None  # type: ignore
        self._socks_port: Optional[int] = None
        self._control_port: Optional[int] = None
        self._data_dir: Optional[str] = None
        self._binary: Optional[str] = None
        self._enabled = CONFIG.ENABLE_TOR
        self._last_error: Optional[str] = None

    def enable(self, on: bool) -> None:
        self._enabled = bool(on)

    def is_enabled(self) -> bool:
        return self._enabled

    def _choose_ports(self) -> tuple[int, int]:
        socks = CONFIG.TOR_SOCKS_PORT or _find_free_port()
        control = CONFIG.TOR_CONTROL_PORT or _find_free_port()
        if socks == control:
            control = _find_free_port()
        return socks, control

    def start(self, wait_seconds: float = 20.0) -> TorStatus:
        if not self._enabled:
            self._last_error = "Tor disabled by configuration"
            return self.status()

        if self._proc is not None:
            return self.status()

        candidates = _candidate_tor_paths()
        if not candidates:
            self._last_error = (
                "Tor binary not found in PATH or common locations. "
                "Set TOR_BIN to the full path of tor (e.g., C:\\Path\\to\\tor.exe)."
            )
            return self.status()

        valid = [c for c in candidates if os.path.isfile(c)]
        binary = valid[0] if valid else None
        if not binary:
            sample = ", ".join(candidates[:5])
            if len(candidates) > 5:
                sample += ", ..."
            self._last_error = (
                f"Tor binary not found. Checked: {sample}. "
                "Set TOR_BIN to the tor executable path to override autodiscovery."
            )
            return self.status()

        self._binary = binary
        self._data_dir = tempfile.mkdtemp(prefix="omni_tor_")
        self._socks_port, self._control_port = self._choose_ports()

        try:
            # Stem on Windows does not support the 'timeout' argument.
            # See error: "You cannot launch tor with a timeout on Windows".
            kwargs = dict(
                tor_cmd=binary,
                take_ownership=True,
                config={
                    "SOCKSPort": str(self._socks_port),
                    "ControlPort": str(self._control_port),
                    "DataDirectory": self._data_dir,
                    "Log": f"{CONFIG.TOR_LOG_LEVEL} stdout",
                },
            )
            if not _is_windows():
                kwargs["timeout"] = wait_seconds
            self._proc = launch_tor_with_config(**kwargs)
        except Exception as e:
            self._last_error = f"Tor launch failed: {e}"
            self._cleanup()
        return self.status()

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            finally:
                self._proc = None
        self._cleanup()

    def _cleanup(self) -> None:
        # do not remove data dir immediately to allow inspection; but here we'll remove
        if self._data_dir and os.path.isdir(self._data_dir):
            try:
                shutil.rmtree(self._data_dir, ignore_errors=True)
            except Exception:
                pass
        self._data_dir = None
        self._socks_port = None
        self._control_port = None

    def status(self) -> TorStatus:
        return TorStatus(
            running=self._proc is not None,
            socks_port=self._socks_port,
            control_port=self._control_port,
            binary=self._binary,
            data_dir=self._data_dir,
            error=self._last_error,
        )

    def proxies(self) -> Optional[Dict[str, str]]:
        if self._proc is None or not self._socks_port:
            return None
        host = "127.0.0.1"
        port = self._socks_port
        proxy = f"socks5h://{host}:{port}"
        return {"http": proxy, "https": proxy}

    def get(self, url: str, timeout: Optional[float] = None) -> requests.Response:
        """Convenience wrapper to GET via Tor."""
        proxies = self.proxies()
        if not proxies:
            raise RuntimeError("Tor is not running; cannot route request")
        headers = {"User-Agent": CONFIG.USER_AGENT, "Accept": "*/*"}
        t = timeout if timeout is not None else CONFIG.DEFAULT_TIMEOUT
        return requests.get(url, headers=headers, timeout=t, verify=CONFIG.VERIFY_SSL, proxies=proxies)


# Singleton-style default manager for convenience
DEFAULT_TOR = TorManager()
