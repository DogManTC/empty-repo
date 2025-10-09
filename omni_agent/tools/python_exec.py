from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from typing import Any, Dict, Optional

from omni_agent.config import CONFIG


def python_exec(code: str, inputs: Optional[Dict[str, Any]] = None, timeout: int = 5, allow_imports: bool = False) -> Dict[str, Any]:
    """Execute small Python snippets in an isolated subprocess.

    - Captures stdout/stderr
    - Returns the value of the last expression (like a REPL), also available as _result
    - Disables imports by default; enable with allow_imports=True
    - Enforces a wall-clock timeout (seconds)
    """
    if not code or not isinstance(code, str):
        return {"status": "error", "error": "code must be a non-empty string"}
    if timeout <= 0 or timeout > 60:
        return {"status": "error", "error": "timeout must be between 1 and 60 seconds"}
    if len(code) > 20000:
        return {"status": "error", "error": "code too long (limit 20k chars)"}

    runner = textwrap.dedent(
        """
        import sys, json, io, contextlib, builtins, traceback, time, ast, types
        import importlib
        import math, statistics, random, re, itertools, functools, collections, decimal, fractions
        from datetime import datetime, date, timedelta

        def _wrap_last_expr_to_result(src: str) -> str:
            try:
                tree = ast.parse(src, mode='exec')
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    last = tree.body[-1]
                    assign = ast.Assign(targets=[ast.Name(id='_result', ctx=ast.Store())], value=last.value)
                    tree.body[-1] = assign
                    ast.fix_missing_locations(tree)
                    return compile(tree, '<pytool>', 'exec')
                return compile(tree, '<pytool>', 'exec')
            except Exception:
                # fallback: just exec as-is
                return compile(src, '<pytool>', 'exec')

        def _safe_builtins(allow_imports=False):
            base = {
                'print': builtins.print,
                'len': builtins.len,
                'range': builtins.range,
                'min': builtins.min,
                'max': builtins.max,
                'sum': builtins.sum,
                'abs': builtins.abs,
                'round': builtins.round,
                'enumerate': builtins.enumerate,
                'list': builtins.list,
                'dict': builtins.dict,
                'set': builtins.set,
                'tuple': builtins.tuple,
                'sorted': builtins.sorted,
                'any': builtins.any,
                'all': builtins.all,
            }
            # Always provide a restricted __import__ that allows only a safe whitelist by default.
            # If allow_imports=True, fall back to the real importer.
            REAL_IMPORT = builtins.__import__

            SAFE_MODULES = {
                'math': math,
                'statistics': statistics,
                'random': random,
                're': re,
                'itertools': itertools,
                'functools': functools,
                'collections': collections,
                'decimal': decimal,
                'fractions': fractions,
                'datetime': __import__('datetime'),
            }

            def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                if allow_imports:
                    return REAL_IMPORT(name, globals, locals, fromlist, level)
                # allow safe top-level modules only
                root = name.split('.')[0]
                if root in SAFE_MODULES:
                    return SAFE_MODULES[root]
                raise ImportError(f"Import blocked: {name}")

            base['__import__'] = _restricted_import
            return base

        data = json.load(sys.stdin)
        code = data.get('code') or ''
        user_inputs = data.get('inputs') or {}
        allow_imports = bool(data.get('allow_imports'))

        g = {
            '__builtins__': _safe_builtins(allow_imports),
            # Safe, pre-imported modules (no file/network access):
            'math': math,
            'statistics': statistics,
            'random': random,
            're': re,
            'itertools': itertools,
            'functools': functools,
            'collections': collections,
            'decimal': decimal,
            'fractions': fractions,
            'datetime': datetime,
            'date': date,
            'timedelta': timedelta,
        }
        # locals receives user inputs
        l = {}
        if isinstance(user_inputs, dict):
            l.update(user_inputs)

        stdout_io, stderr_io = io.StringIO(), io.StringIO()
        start = time.perf_counter()
        _result = None
        try:
            compiled = _wrap_last_expr_to_result(code)
            with contextlib.redirect_stdout(stdout_io), contextlib.redirect_stderr(stderr_io):
                exec(compiled, g, l)
            _result = l.get('_result', None)
            ok = True
            err = None
        except Exception as e:
            ok = False
            err = ''.join(traceback.format_exception_only(type(e), e)).strip()
        dur_ms = int((time.perf_counter() - start) * 1000)

        out = {
            'status': 'ok' if ok else 'error',
            'stdout': stdout_io.getvalue(),
            'stderr': stderr_io.getvalue(),
            'result': _result,
            'type': type(_result).__name__ if _result is not None else None,
            'duration_ms': dur_ms,
        }
        if not ok and err:
            out['error'] = err
        # Ensure JSON-serializable result
        try:
            json.dumps(out, ensure_ascii=False, default=str)
        except Exception:
            out['result'] = str(out.get('result'))
        sys.stdout.write(json.dumps(out, ensure_ascii=False, default=str))
        sys.stdout.flush()
        """
    )

    payload = {
        "code": code,
        "inputs": inputs or {},
        "allow_imports": bool(allow_imports),
    }

    try:
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-I", "-u", "-c", runner],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"execution timed out after {timeout}s"}
    except Exception as e:
        return {"status": "error", "error": f"failed to start subprocess: {e}"}

    # The child writes JSON to stdout; stderr may contain interpreter noise if failure
    out_text = proc.stdout or ""
    try:
        result = json.loads(out_text)
    except Exception:
        # fallback when child printed unexpected output
        trimmed = (out_text or proc.stderr or "").strip()
        if len(trimmed) > CONFIG.MAX_TOOL_CONTENT_CHARS:
            trimmed = trimmed[: CONFIG.MAX_TOOL_CONTENT_CHARS]
        return {"status": "error", "error": "malformed child output", "child_out": trimmed}

    # Truncate large stdout/stderr for safety
    for k in ("stdout", "stderr"):
        if isinstance(result.get(k), str) and len(result[k]) > CONFIG.MAX_TOOL_CONTENT_CHARS:
            result[k] = result[k][: CONFIG.MAX_TOOL_CONTENT_CHARS]
    return result


PYTHON_EXEC_TOOL = {
    "type": "function",
    "function": {
        "name": "python_exec",
        "description": "Execute sandboxed Python with safe modules. You may import only: math, statistics, random, re, itertools, functools, collections, decimal, fractions, datetime (date/timedelta). All other imports require allow_imports=true.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code snippet. Last expression's value is returned as result."},
                "inputs": {"type": "object", "description": "Optional variables available to the code."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (1-60, default 5)."},
                "allow_imports": {"type": "boolean", "description": "Allow 'import' statements (default false)."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    },
}
