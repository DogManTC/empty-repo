from __future__ import annotations

import json
from io import BytesIO
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from pdfminer.high_level import extract_text as pdf_extract_text  # type: ignore
from requests.exceptions import RequestException, Timeout, SSLError, ConnectionError as ConnErr
import trafilatura

from omni_agent.config import CONFIG
from omni_agent.utils.http import fetch_raw, is_pdf_response
from omni_agent.utils.text import tidy_text, extract_links_html


def duck_search(query: str, max_results: int = 5) -> Dict[str, List[Dict[str, str]]]:
    results: List[Dict[str, str]] = []
    with DDGS() as ddgs:
        for item in ddgs.text(
                query,
                region="us-en",
                safesearch="moderate",
                timelimit=None,
                max_results=max_results,
        ):
            results.append(
                {
                    "title": item.get("title") or "",
                    "url": item.get("href") or "",
                    "content": item.get("body") or "",
                }
            )
    return {"results": results}


DUCK_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "duck_search",
        "description": "Search the web using DuckDuckGo and return relevant results.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query string."},
                "max_results": {"type": "integer", "description": "Max results to return (default 5)."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


def fetch_url(url: str, max_chars: int = 100000) -> Dict[str, Any]:
    """
    Keyless page fetcher:
      - Fetch URL with requests
      - If PDF -> pdfminer.six (if available)
      - Else try trafilatura for main content & title
      - Fallback to BeautifulSoup text
    Returns: { title, content, links }
    """
    try:
        resp = fetch_raw(url)
    except (Timeout, SSLError, ConnErr) as e:
        return {"title": "", "content": f"ERROR: network error: {e}", "links": []}
    except RequestException as e:
        return {"title": "", "content": f"ERROR: request error: {e}", "links": []}

    if is_pdf_response(resp):
        try:
            text = pdf_extract_text(BytesIO(resp.content))
            return {
                "title": "PDF Document",
                "content": tidy_text(text)[:max_chars],
                "links": [],
            }
        except Exception:
            return {"title": "PDF Document", "content": "Unable to extract text from PDF.", "links": []}

    # HTML (or text)
    html = resp.text or ""

    # Try trafilatura first
    extracted = trafilatura.extract(
        html,
        url=resp.url,
        include_links=False,
        include_comments=False,
        target_language="en",
    )

    # Try to get a best-effort title from HTML
    soup_for_title = BeautifulSoup(html, "lxml")
    title_guess = soup_for_title.title.string.strip() if soup_for_title.title and soup_for_title.title.string else "Untitled"
    links = extract_links_html(html, resp.url)

    if extracted and extracted.strip():
        return {
            "title": title_guess or "Untitled",
            "content": tidy_text(extracted)[:max_chars],
            "links": links,
        }

    # Fallback: soup text
    for tag in soup_for_title(["script", "style", "noscript"]):
        tag.decompose()
    text = soup_for_title.get_text("\n")

    return {
        "title": title_guess,
        "content": tidy_text(text)[:max_chars],
        "links": links,
    }


FETCH_URL_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": "Fetch a URL and return its main content, title, and outlinks (keyless).",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch."},
                "max_chars": {"type": "integer", "description": "Cap extracted content characters (default 100000)."},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
}
