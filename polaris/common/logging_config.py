"""One place every module gets its logger from. Two rules this enforces:

1. No module calls logging.basicConfig() itself -- only the entry points
   (scripts/, run_evaluation.py's __main__) configure handlers/format. A
   library module that calls basicConfig() fights with whatever the
   application (or pytest) already configured, which is how logging
   silently goes missing in practice.
2. get_logger(__name__) is the only way any module obtains a logger, so a
   grep for `logging.getLogger` outside this file means someone bypassed
   the convention.

Every failure boundary in this repo (network, file, LLM call, database)
logs at WARNING or ERROR with enough context (URL, source_id, file path)
to diagnose without re-running -- logging replaces the silent `except:
pass` / `except: return None` pattern that produces a result
indistinguishable from success.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure(level: int = logging.INFO) -> None:
    """Call once, from an entry point (a script's __main__, or a test
    session's conftest if you want logs during pytest -s). Calling it more
    than once is harmless -- it's a no-op after the first call."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
    ))
    root = logging.getLogger("polaris")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
