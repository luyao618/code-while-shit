from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone
from typing import TextIO

# ANSI color helpers
_RESET = "\x1b[0m"
_COLORS = {
    "inbound": "\x1b[36m",     # cyan
    "outbound": "\x1b[32m",    # green
    "delta": "\x1b[37m",       # white
    "status": "\x1b[33m",      # yellow
    "error": "\x1b[31m",       # red
    "banner": "\x1b[35m",      # magenta
}


def _no_color() -> bool:
    return os.environ.get("NO_COLOR") not in (None, "", "0")


class TerminalSink:
    """Thread-safe stdout writer for foreground serve mode.

    Every write_line is atomic: under concurrent contention, no two writes interleave.
    """

    def __init__(self, stream: TextIO | None = None, *, use_color: bool | None = None):
        self._stream = stream if stream is not None else sys.stdout
        self._lock = threading.Lock()
        self._use_color = use_color if use_color is not None else not _no_color()

    def write_line(self, text: str, *, kind: str = "status") -> None:
        """Write a single timestamped line atomically.

        kind is one of: inbound, outbound, delta, status, error, banner.
        """
        ts = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S.%f")[:-3]
        color = _COLORS.get(kind, "") if self._use_color else ""
        reset = _RESET if self._use_color and color else ""
        line = f"[{ts}] {color}{text}{reset}\n"
        with self._lock:
            self._stream.write(line)
            self._stream.flush()

    def inbound(self, text: str) -> None:
        self.write_line(f"feishu\u2192 {text}", kind="inbound")

    def outbound(self, text: str) -> None:
        self.write_line(f"\u2192feishu {text}", kind="outbound")

    def delta(self, text: str) -> None:
        self.write_line(f"agent: {text}", kind="delta")

    def status(self, text: str) -> None:
        self.write_line(text, kind="status")

    def error(self, text: str) -> None:
        self.write_line(text, kind="error")

    def banner(self, text: str) -> None:
        self.write_line(text, kind="banner")
