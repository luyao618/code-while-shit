import io
import re
import threading
from cws.terminal_sink import TerminalSink


def test_write_line_basic():
    buf = io.StringIO()
    sink = TerminalSink(stream=buf, use_color=False)
    sink.write_line("hello", kind="status")
    out = buf.getvalue()
    assert "hello" in out
    assert re.match(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\] hello\n$", out)


def test_no_color_disables_ansi(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setenv("NO_COLOR", "1")
    sink = TerminalSink(stream=buf)
    sink.write_line("x", kind="error")
    assert "\x1b[" not in buf.getvalue()


def test_concurrency_8x200_no_interleave():
    """8 writer threads × 200 writes each → 1600 well-formed lines, no torn output."""
    buf = io.StringIO()
    sink = TerminalSink(stream=buf, use_color=False)

    def writer(idx: int) -> None:
        for j in range(200):
            sink.write_line(f"writer{idx}-line{j}", kind="status")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = buf.getvalue().splitlines()
    assert len(lines) == 1600, f"expected 1600 lines, got {len(lines)}"
    pattern = re.compile(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\] writer\d+-line\d+$")
    for i, ln in enumerate(lines):
        assert pattern.match(ln), f"line {i} torn or malformed: {ln!r}"


def test_kind_methods():
    buf = io.StringIO()
    sink = TerminalSink(stream=buf, use_color=False)
    sink.inbound("hello")
    sink.outbound("world")
    sink.delta("token")
    sink.banner("start")
    out = buf.getvalue()
    assert "feishu\u2192 hello" in out
    assert "\u2192feishu world" in out
    assert "agent: token" in out
    assert "start" in out
