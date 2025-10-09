from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from omni_agent.context import get_home_dir


def _normalize_home_and_path(rel_or_abs: str) -> Optional[str]:
    home = get_home_dir()
    if not home:
        return None
    p = rel_or_abs
    if not os.path.isabs(p):
        p = os.path.join(home, p)
    try:
        p = os.path.abspath(p)
    except Exception:
        return None
    # enforce within home
    try:
        home_abs = os.path.abspath(home)
        if os.path.commonpath([home_abs, p]) != home_abs:
            return None
    except Exception:
        return None
    return p


def _iter_home_files() -> List[str]:
    home = get_home_dir()
    if not home or not os.path.isdir(home):
        return []
    out: List[str] = []
    for root, _dirs, files in os.walk(home):
        for f in files:
            out.append(os.path.join(root, f))
    return out


def search_files(name: Optional[str] = None,
                 ext: Optional[str] = None,
                 contains: Optional[str] = None,
                 case_sensitive: bool = False,
                 regex: bool = False,
                 max_results: int = 50) -> Dict[str, Any]:
    """Search files under the current home directory.

    - Filters:
      - name: substring or regex (basename only)
      - ext: file extension like 'txt' or '.txt'
      - contains: text substring to search in supported text formats
    - Returns up to max_results with path, size, modified.
    """
    home = get_home_dir()
    if not home:
        return {"status": "error", "error": "home directory not set. Use /home-dir to set it."}
    if not os.path.isdir(home):
        return {"status": "error", "error": f"home directory not found: {home}"}

    flags = 0 if case_sensitive else re.IGNORECASE
    name_re = None
    if name:
        name_re = re.compile(name if regex else re.escape(name), flags)

    ext_norm = None
    if ext:
        ext_norm = ext.lower()
        if not ext_norm.startswith('.'):
            ext_norm = '.' + ext_norm

    results: List[Dict[str, Any]] = []
    scanned = 0
    for path in _iter_home_files():
        try:
            bn = os.path.basename(path)
            if name_re and not name_re.search(bn):
                continue
            if ext_norm and not bn.lower().endswith(ext_norm):
                continue

            if contains:
                # Only scan simple text-like formats
                bn_low = bn.lower()
                if any(bn_low.endswith(suf) for suf in ('.txt', '.md', '.markdown', '.json', '.csv', '.py', '.html', '.htm', '.log')):
                    try:
                        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                            data = f.read(200000)  # cap
                            if (contains in data) if case_sensitive else (contains.lower() in data.lower()):
                                pass
                            else:
                                continue
                    except Exception:
                        continue
                else:
                    continue

            st = os.stat(path)
            results.append({
                "path": path,
                "size_bytes": int(st.st_size),
                "modified": int(st.st_mtime),
            })
            if len(results) >= max_results:
                break
        except Exception:
            continue
        finally:
            scanned += 1

    return {"status": "ok", "home": home, "results": results, "scanned": scanned}


SEARCH_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "search_files",
        "description": "Search files under the current home directory set via /home-dir. Supports name substring/regex, extension, and basic content search for text formats.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name substring or regex (basename)."},
                "ext": {"type": "string", "description": "File extension filter, e.g., 'txt' or '.txt'."},
                "contains": {"type": "string", "description": "Search file contents (text formats)."},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive matching (default false)."},
                "regex": {"type": "boolean", "description": "Treat 'name' as regex (default false)."},
                "max_results": {"type": "integer", "description": "Maximum files to return (default 50)."}
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

