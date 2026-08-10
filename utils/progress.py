"""Human-readable crawl progress for interactive terminals.

The structured JSON log remains the machine-readable record. This module only
renders a live view for an operator watching a run, and it degrades to plain
lines when the stream is redirected, so piped output stays parseable.
"""

from __future__ import annotations

import shutil
import sys
import time
from typing import Any, TextIO

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"


def _enable_windows_ansi() -> None:
    """Windows 10 consoles need virtual terminal processing switched on."""

    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # -11 is STD_OUTPUT_HANDLE, -12 is STD_ERROR_HANDLE.
        for handle_id in (-11, -12):
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # A console without VT support simply renders plainly.
        pass


def _stream_supports(stream: TextIO, sample: str) -> bool:
    encoding = getattr(stream, "encoding", None) or ""
    try:
        sample.encode(encoding)
    except (LookupError, UnicodeEncodeError, TypeError):
        return False
    return True


class ProgressReporter:
    """Render crawl progress; a disabled reporter is a no-op."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.stream = stream or sys.stderr
        if enabled is None:
            enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = enabled
        self.interactive = self.enabled and bool(
            getattr(self.stream, "isatty", lambda: False)()
        )
        if self.interactive:
            _enable_windows_ansi()
        self.unicode = self.enabled and _stream_supports(self.stream, "█░›")
        self.color = self.interactive

        self.total = 0
        self.done = 0
        self.products = 0
        self.variants = 0
        self.cached = 0
        self.errors = 0
        self.started_at = time.monotonic()
        self._line_open = False

    # ----- internals ---------------------------------------------------

    def _paint(self, text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if self.color else text

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (ValueError, OSError):  # Stream closed underneath us.
            self.enabled = False

    def _clear_status(self) -> None:
        if self._line_open and self.interactive:
            width = shutil.get_terminal_size((100, 24)).columns
            self._write("\r" + " " * (width - 1) + "\r")
        self._line_open = False

    def _emit(self, text: str) -> None:
        """Print a permanent line above the live status line."""

        if not self.enabled:
            return
        self._clear_status()
        self._write(text + "\n")
        self._render_status()

    def _bar(self, fraction: float, width: int = 22) -> str:
        filled = int(round(fraction * width))
        if self.unicode:
            return "█" * filled + "░" * (width - filled)
        return "#" * filled + "-" * (width - filled)

    @staticmethod
    def _clock(seconds: float) -> str:
        minutes, secs = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _render_status(self) -> None:
        if not (self.enabled and self.interactive and self.total):
            return
        elapsed = time.monotonic() - self.started_at
        fraction = self.done / self.total if self.total else 0.0
        rate = self.done / elapsed if elapsed > 0 else 0.0
        remaining = (self.total - self.done) / rate if rate > 0 else 0.0
        errors = (
            self._paint(f"err {self.errors}", _RED)
            if self.errors
            else self._paint("err 0", _DIM)
        )
        status = (
            f"  {self._paint(self._bar(fraction), _CYAN)} "
            f"{self.done}/{self.total} {int(fraction * 100):3d}%  "
            f"{self._paint(f'prod {self.products}', _DIM)} "
            f"{self._paint(f'var {self.variants}', _DIM)} "
            f"{self._paint(f'cache {self.cached}', _DIM)} {errors}  "
            f"{self._paint(self._clock(elapsed), _DIM)}"
            f"{self._paint(f' eta {self._clock(remaining)}', _DIM) if rate > 0 else ''}"
        )
        width = shutil.get_terminal_size((100, 24)).columns
        self._write("\r" + status[: width - 1])
        self._line_open = True

    # ----- lifecycle hooks ---------------------------------------------

    def run_started(self, *, categories: list[str], mode: str) -> None:
        if not self.enabled:
            return
        self.started_at = time.monotonic()
        self._emit("")
        self._emit(self._paint("  Safco Dental catalog crawl", _BOLD))
        self._emit(self._paint(f"  categories: {', '.join(categories)}   mode: {mode}", _DIM))
        self._emit("")

    def robots(self, *, decision: str, enforced: bool) -> None:
        colour = _GREEN if decision == "ALLOWED" else _RED
        note = "enforced" if enforced else "not enforced"
        self._emit(f"  robots      {self._paint(decision, colour)} ({note})")

    def category_discovered(
        self, *, name: str, count: int, method: str, degraded: bool
    ) -> None:
        label = self._paint(method, _YELLOW if degraded else _DIM)
        self._emit(f"  discovery   {name:<20} {count:>4} families   {label}")

    def category_failed(self, *, name: str, error: str) -> None:
        self._emit(f"  discovery   {name:<20} {self._paint('FAILED', _RED)}  {error[:60]}")

    def crawl_started(self, *, total: int, note: str | None = None) -> None:
        self.total = total
        if not self.enabled:
            return
        self._emit("")
        if total == 0:
            # "0 products" on its own reads like a failure; say why there is
            # nothing to do.
            self._emit(self._paint("  nothing to extract", _YELLOW))
            if note:
                self._emit(self._paint(f"  {note}", _DIM))
            return
        self._emit(self._paint(f"  extracting {total} product families", _BOLD))
        self._render_status()

    def product_done(
        self,
        *,
        category: str,
        url: str,
        variants: int = 0,
        from_cache: bool = False,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        self.done += 1
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if ok:
            self.products += 1
            self.variants += variants
            if from_cache:
                self.cached += 1
            mark = self._paint("ok  ", _GREEN)
            source = self._paint("cache" if from_cache else "http ", _DIM)
            detail = f"{variants:>3} var"
        else:
            self.errors += 1
            mark = self._paint("fail", _RED)
            source = self._paint("     ", _DIM)
            detail = self._paint((error or "")[:44], _RED)
        arrow = "›" if self.unicode else ">"
        self._emit(
            f"  {mark} {source} {category[:16]:<16} {arrow} {slug[:38]:<38} {detail}"
        )

    def shadow(self, *, status: str, sample: int, agreement: float | None) -> None:
        if status == "completed" and agreement is not None:
            colour = _GREEN if agreement >= 0.8 else _YELLOW
            text = self._paint(f"{agreement:.1%} agreement on {sample} sampled", colour)
        else:
            text = self._paint(f"{status} ({sample} requested)", _DIM)
        self._emit(f"  shadow LLM  {text}")

    def finished(
        self,
        *,
        status: str,
        outputs: dict[str, Any],
        stored: tuple[int, int] | None = None,
    ) -> None:
        if not self.enabled:
            return
        self._clear_status()
        colour = {"completed": _GREEN, "partial": _YELLOW}.get(status, _RED)
        elapsed = self._clock(time.monotonic() - self.started_at)
        self._emit("")
        if self.total == 0 and stored is not None:
            # Report the catalogue that exists, not the zero rows this run added.
            products, variants = stored
            self._emit(
                f"  {self._paint(status.upper(), colour)}  no work to do; "
                f"database already holds {products} products and {variants} variants"
            )
        else:
            self._emit(
                f"  {self._paint(status.upper(), colour)}  "
                f"{self.products} products, {self.variants} variants, "
                f"{self.errors} errors in {elapsed}"
            )
        for label, path in outputs.items():
            self._emit(self._paint(f"    {label:<10} {path}", _DIM))
        self._emit("")
