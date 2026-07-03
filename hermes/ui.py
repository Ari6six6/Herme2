"""Tiny ANSI palette for the REPL.

Colors switch off automatically when stdout is not a tty, NO_COLOR is set,
or TERM=dumb, so piped output and dumb terminals stay clean. Anything that
ends up in a prompt for the model (e.g. the gpu_status env line) must stay
uncolored — only paint what goes straight to the operator's screen.
"""

from __future__ import annotations

import os
import sys


def _detect() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


ENABLED = _detect()


def _paint(code: str):
    def fn(text: object) -> str:
        if not ENABLED:
            return str(text)
        return f"\x1b[{code}m{text}\x1b[0m"
    return fn


bold = _paint("1")
dim = _paint("2")
red = _paint("31")
green = _paint("32")
yellow = _paint("33")
magenta = _paint("35")
cyan = _paint("36")
