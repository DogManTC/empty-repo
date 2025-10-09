from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, List

from bs4 import BeautifulSoup


def to_str_preview(obj: Any, max_chars: int = 12000) -> str:
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return s[:max_chars]


def tidy_text(s: str) -> str:
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def extract_links_html(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for a in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(base_url, a["href"]) if base_url else a["href"]
        urls.append(href)
    # de-dup while preserving order
    seen = set()
    uniq = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:100]
