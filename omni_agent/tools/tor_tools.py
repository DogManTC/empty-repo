from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from bs4 import BeautifulSoup
from requests.exceptions import RequestException, Timeout, SSLError, ConnectionError as ConnErr

from omni_agent.config import CONFIG
from omni_agent.tor.tor_client import DEFAULT_TOR
from omni_agent.utils.http import fetch_raw
from omni_agent.utils.text import tidy_text


def _tor_proxies_or_error() -> Dict[str, str] | Dict[str, Any]:
    status = DEFAULT_TOR.status()
    if not status.running:
        st = DEFAULT_TOR.start()
        if not st.running:
            return {"status": "error", "error": st.error or "Tor failed to start"}
    proxies = DEFAULT_TOR.proxies()
    if not proxies:
        return {"status": "error", "error": "Tor proxies unavailable"}
    return proxies


def tor_search(query: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """Basic Tor search using Ahmia (clearnet endpoint accessed via Tor) or a searx-compatible base URL if provided.
    This is a simplified approach that fetches the search results page and returns visible text.
    """
    if not query or not isinstance(query, str):
        return {"status": "error", "error": "query must be a non-empty string"}

    proxies_or_err = _tor_proxies_or_error()
    if isinstance(proxies_or_err.get("status"), str):  # type: ignore[attr-defined]
        return proxies_or_err  # error dict
    proxies: Dict[str, str] = proxies_or_err  # type: ignore[assignment]

    engine = base_url or "https://ahmia.fi/search/"
    url = f"{engine}?q={query}"
    try:
        resp = fetch_raw(url, proxies=proxies)
    except (Timeout, SSLError, ConnErr) as e:
        return {"status": "error", "error": f"network error: {e}"}
    except RequestException as e:
        return {"status": "error", "error": f"request error: {e}"}

    soup = BeautifulSoup(resp.text or "", "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else "Search Results"
    text = soup.get_text("\n")
    return {
        "status": "ok",
        "results": [{
            "title": title,
            "url": resp.url,
            "content": tidy_text(text)[:CONFIG.MAX_TOOL_CONTENT_CHARS],
        }],
        "meta": {
            "query": query,
            "search_url": resp.url,
            "engine_base": engine,
            "page_title": title,
            "page_text": tidy_text(text)[:CONFIG.MAX_TOOL_CONTENT_CHARS],
        },
    }


def tor_fetch(url: str) -> Dict[str, Any]:
    if not url or not isinstance(url, str):
        return {"status": "error", "error": "url must be a non-empty string"}

    proxies_or_err = _tor_proxies_or_error()
    if isinstance(proxies_or_err.get("status"), str):  # type: ignore[attr-defined]
        return proxies_or_err  # error dict
    proxies: Dict[str, str] = proxies_or_err  # type: ignore[assignment]

    try:
        resp = fetch_raw(url, proxies=proxies)
    except (Timeout, SSLError, ConnErr) as e:
        return {"status": "error", "error": f"network error: {e}"}
    except RequestException as e:
        return {"status": "error", "error": f"request error: {e}"}

    soup = BeautifulSoup(resp.text or "", "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else "Untitled"
    text = soup.get_text("\n")
    return {
        "status": "ok",
        "title": title,
        "url": resp.url or url,
        "content": tidy_text(text)[:CONFIG.MAX_TOOL_CONTENT_CHARS],
    }


def onion_up(address: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    """Return true/false for whether a .onion is reachable via Tor.

    - Accepts a full URL (http/https) or a bare hostname like "example.onion".
    - Treats any HTTP response (2xx-5xx) as "up"; connection/timeout errors are "down".
    - Uses a short timeout if provided, else the default.
    """
    if not address or not isinstance(address, str):
        return {"status": "error", "error": "address must be a non-empty string"}

    # Normalize to URL
    addr = address.strip()
    parsed = urlparse(addr if "://" in addr else f"http://{addr}")
    host = parsed.hostname or ""
    if ".onion" not in host.lower():
        return {"status": "error", "error": "address must be a .onion host or URL"}
    scheme = parsed.scheme or "http"
    url = f"{scheme}://{host}"

    # Ensure Tor proxies
    proxies_or_err = _tor_proxies_or_error()
    if isinstance(proxies_or_err.get("status"), str):  # type: ignore[attr-defined]
        return proxies_or_err  # error dict
    proxies: Dict[str, str] = proxies_or_err  # type: ignore[assignment]

    t = timeout if timeout is not None else min(CONFIG.DEFAULT_TIMEOUT, 20)
    headers = {"User-Agent": CONFIG.USER_AGENT, "Accept": "*/*"}

    def _try(method: str, test_url: str) -> tuple[bool, Optional[int], Optional[str]]:
        try:
            resp = requests.request(method, test_url, headers=headers, timeout=t, allow_redirects=False, verify=CONFIG.VERIFY_SSL, proxies=proxies, stream=True)
            status_code = resp.status_code
            resp.close()
            # Any response means the service is up (even 4xx/5xx)
            return True, status_code, None
        except (requests.exceptions.Timeout, requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
            return False, None, str(e)
        except requests.exceptions.RequestException as e:
            return False, None, str(e)

    # Prefer HEAD to avoid downloading content; fallback to GET
    up, code, err = _try("HEAD", url)
    if not up:
        # If HTTPS failed due to SSL, try HTTP fallback once
        if scheme.lower() == "https" and isinstance(err, str) and "SSL" in err:
            http_url = f"http://{host}"
            up, code, err = _try("HEAD", http_url)
            if not up:
                up, code, err = _try("GET", http_url)
        else:
            up, code, err = _try("GET", url)

    return {
        "status": "ok",
        "up": bool(up),
        "url": url,
        "http_status": code,
        "error": None if up else err,
    }


TOR_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "tor_search",
        "description": "Search via the local Tor client (Ahmia/searx-compatible endpoints). Returns a page of results text and metadata.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query string."},
                "base_url": {"type": "string", "description": "Optional search engine base URL."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

TOR_FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "tor_fetch",
        "description": "Fetch a single page via the local Tor client.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch."},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
}

ONION_UP_TOOL = {
    "type": "function",
    "function": {
        "name": "onion_up",
        "description": "Return true/false if a given .onion host/URL is reachable via Tor.",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "A .onion hostname or full URL."},
                "timeout": {"type": "integer", "description": "Optional timeout seconds (default <= 20)."},
            },
            "required": ["address"],
            "additionalProperties": False,
        },
    },
}
