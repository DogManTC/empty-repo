from __future__ import annotations

from io import BytesIO
from typing import Dict

import requests

from omni_agent.config import CONFIG


def is_pdf_response(resp: requests.Response) -> bool:
    ctype = (resp.headers.get("Content-Type") or "").lower()
    return "application/pdf" in ctype or resp.url.lower().endswith(".pdf")


def fetch_raw(url: str, timeout: int | None = None, allow_redirects: bool = True, proxies: Dict[str, str] | None = None) -> requests.Response:
    headers = {"User-Agent": CONFIG.USER_AGENT, "Accept": "*/*"}
    t = timeout if timeout is not None else CONFIG.DEFAULT_TIMEOUT
    return requests.get(url, headers=headers, timeout=t, allow_redirects=allow_redirects, verify=CONFIG.VERIFY_SSL, proxies=proxies)
