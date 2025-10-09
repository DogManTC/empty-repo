from __future__ import annotations

import os
from datetime import datetime
from io import BytesIO
from typing import Any, Dict

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text  # type: ignore
import trafilatura

from omni_agent.utils.text import tidy_text, extract_links_html
from omni_agent.context import get_home_dir


def _enforce_home_and_abspath(path: str) -> Dict[str, Any] | str:
    home = get_home_dir()
    p = path
    if home and not os.path.isabs(p):
        p = os.path.join(home, p)
    try:
        p_abs = os.path.abspath(p)
    except Exception:
        return {"status": "error", "error": f"invalid path: {path}"}
    if home:
        try:
            home_abs = os.path.abspath(home)
            if os.path.commonpath([home_abs, p_abs]) != home_abs:
                return {"status": "error", "error": "path outside of home directory; set /home-dir or use a relative path under it"}
        except Exception:
            return {"status": "error", "error": "home directory check failed"}
    return p_abs


def _file_meta(abs_path: str) -> Dict[str, Any]:
    try:
        st = os.stat(abs_path)
        mtime = datetime.fromtimestamp(st.st_mtime).isoformat()
        return {"path": abs_path, "size_bytes": int(st.st_size), "modified": mtime}
    except Exception:
        return {"path": abs_path}


def load_file(path: str, max_chars: int = 100000) -> Dict[str, Any]:
    """Load a local file (.txt, .md, .html, .htm, .pdf) and extract readable content.

    Returns a dict with status, kind, title, content, links (for HTML), and basic metadata.
    """
    if not path or not isinstance(path, str):
        return {"status": "error", "error": "path must be a non-empty string"}

    res = _enforce_home_and_abspath(path)
    if isinstance(res, dict):
        return res
    abs_path = res
    if not os.path.isfile(abs_path):
        return {"status": "error", "error": f"file not found: {abs_path}"}

    # Soft cap extremely large files to avoid memory churn
    try:
        size = os.path.getsize(abs_path)
        if size > 25 * 1024 * 1024:  # 25 MB
            return {"status": "error", "error": f"file too large: {size} bytes (limit 25MB)"}
    except Exception:
        pass

    ext = os.path.splitext(abs_path)[1].lower()

    try:
        with open(abs_path, "rb") as f:
            data = f.read()
    except Exception as e:
        return {"status": "error", "error": f"failed to read file: {e}"}

    meta = _file_meta(abs_path)

    # PDF
    if ext == ".pdf":
        try:
            text = pdf_extract_text(BytesIO(data))
            return {
                "status": "ok",
                "kind": "pdf",
                "title": os.path.basename(abs_path) or "PDF Document",
                "content": tidy_text(text)[:max_chars],
                "links": [],
                "meta": meta,
            }
        except Exception:
            return {"status": "error", "error": "unable to extract text from PDF", "meta": meta}

    # HTML
    if ext in {".html", ".htm", ".xhtml"}:
        try:
            html = data.decode("utf-8", errors="replace")
            extracted = trafilatura.extract(
                html,
                url=f"file://{abs_path}",
                include_links=False,
                include_comments=False,
                target_language="en",
            )
            soup_for_title = BeautifulSoup(html, "lxml")
            title_guess = soup_for_title.title.string.strip() if soup_for_title.title and soup_for_title.title.string else os.path.basename(abs_path) or "Untitled"
            links = extract_links_html(html, f"file://{abs_path}")
            if extracted and extracted.strip():
                return {
                    "status": "ok",
                    "kind": "html",
                    "title": title_guess,
                    "content": tidy_text(extracted)[:max_chars],
                    "links": links,
                    "meta": meta,
                }
            # Fallback: plain text from soup
            for tag in soup_for_title(["script", "style", "noscript"]):
                tag.decompose()
            text = soup_for_title.get_text("\n")
            return {
                "status": "ok",
                "kind": "html",
                "title": title_guess,
                "content": tidy_text(text)[:max_chars],
                "links": links,
                "meta": meta,
            }
        except Exception as e:
            return {"status": "error", "error": f"html parse error: {e}", "meta": meta}

    # Text-like (.txt, .md, .markdown)
    if ext in {".txt", ".md", ".markdown"} or True:
        try:
            text = data.decode("utf-8", errors="replace")
            return {
                "status": "ok",
                "kind": "text",
                "title": os.path.basename(abs_path) or "Text Document",
                "content": tidy_text(text)[:max_chars],
                "links": [],
                "meta": meta,
            }
        except Exception as e:
            return {"status": "error", "error": f"text decode error: {e}", "meta": meta}


LOAD_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "load_file",
        "description": "Load a local file (.txt/.md/.html/.pdf) and return structured text and metadata.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the local file."},
                "max_chars": {"type": "integer", "description": "Cap extracted content characters (default 100000)."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}
