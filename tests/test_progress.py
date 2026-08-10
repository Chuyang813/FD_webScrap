"""The progress view must stay silent when disabled and never break a run."""

from __future__ import annotations

import io

from utils.progress import ProgressReporter


class FakeStream(io.StringIO):
    """StringIO with a settable encoding and controllable isatty()."""

    def __init__(self, *, encoding: str = "utf-8", tty: bool = False) -> None:
        super().__init__()
        self._encoding = encoding
        self._tty = tty

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._encoding

    def isatty(self) -> bool:
        return self._tty


class FakeTTY(FakeStream):
    """A stream that claims to be an interactive terminal."""

    def __init__(self) -> None:
        super().__init__(encoding="utf-8", tty=True)


def test_disabled_reporter_writes_nothing() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(enabled=False, stream=stream)

    reporter.run_started(categories=["gloves"], mode="offline")
    reporter.robots(decision="ALLOWED", enforced=True)
    reporter.category_discovered(name="gloves", count=98, method="algolia", degraded=False)
    reporter.crawl_started(total=98)
    reporter.product_done(category="gloves", url="https://x/product/a", variants=3)
    reporter.finished(status="completed", outputs={"json": "out.json"})

    assert stream.getvalue() == ""


def test_non_tty_stream_emits_plain_lines_without_escape_codes() -> None:
    stream = FakeStream(encoding="utf-8", tty=False)
    reporter = ProgressReporter(enabled=True, stream=stream)

    reporter.category_discovered(name="gloves", count=98, method="algolia", degraded=False)
    output = stream.getvalue()

    assert "gloves" in output
    assert "98" in output
    assert "\033[" not in output, "redirected output must stay free of ANSI codes"
    assert "\r" not in output, "redirected output must not carry carriage returns"


def test_counters_track_successes_failures_and_cache_hits() -> None:
    reporter = ProgressReporter(enabled=True, stream=io.StringIO())
    reporter.crawl_started(total=3)

    reporter.product_done(category="gloves", url="https://x/product/a", variants=4, from_cache=True)
    reporter.product_done(category="gloves", url="https://x/product/b", variants=2)
    reporter.product_done(category="sutures", url="https://x/product/c", ok=False, error="boom")

    assert reporter.done == 3
    assert reporter.products == 2
    assert reporter.variants == 6
    assert reporter.cached == 1
    assert reporter.errors == 1


def test_ascii_stream_falls_back_from_block_characters() -> None:
    stream = FakeStream(encoding="ascii", tty=False)
    reporter = ProgressReporter(enabled=True, stream=stream)

    assert reporter.unicode is False
    assert set(reporter._bar(0.5)) <= {"#", "-"}


def test_tty_stream_renders_a_status_bar() -> None:
    stream = FakeTTY()
    reporter = ProgressReporter(enabled=True, stream=stream)
    reporter.crawl_started(total=10)
    reporter.product_done(category="gloves", url="https://x/product/a", variants=1)

    assert "\r" in stream.getvalue(), "an interactive stream should redraw in place"
    assert "1/10" in stream.getvalue()


def test_closed_stream_disables_reporter_instead_of_raising() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(enabled=True, stream=stream)
    stream.close()

    reporter.product_done(category="gloves", url="https://x/product/a", variants=1)

    assert reporter.enabled is False
