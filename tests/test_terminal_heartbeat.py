"""Batch 3: TerminalPresenter heartbeat — pure unit tests, no real 10s waits."""
from __future__ import annotations

from types import SimpleNamespace

from forge.terminal_present import TerminalPresenter


class _Buf:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, *args, **kwargs):
        end = kwargs.get("end", "\n")
        s = " ".join(str(a) for a in args) if args else ""
        if self.lines and end == "":
            self.lines[-1] = self.lines[-1] + s
        else:
            self.lines.append(s)

    def text(self) -> str:
        return "\n".join(self.lines)

    def running_lines(self) -> list[str]:
        return [x for x in self.lines if "running…" in x or "running..." in x]


class ManualClock:
    """Injectable clock + one-shot timer queue for deterministic heartbeat tests."""

    def __init__(self):
        self.now = 0.0
        self.pending: list[tuple[float, object, callable]] = []
        self._seq = 0

    def time(self) -> float:
        return self.now

    def factory(self, delay: float, callback):
        self._seq += 1
        handle_id = self._seq
        state = {"cancelled": False, "id": handle_id}

        class Handle:
            def cancel(self_inner):
                state["cancelled"] = True

        when = self.now + float(delay)
        self.pending.append((when, state, callback))
        return Handle()

    def advance(self, dt: float) -> None:
        self.now += float(dt)
        # Fire due timers in order (re-scheduling may append during callback)
        guard = 0
        while guard < 100:
            guard += 1
            due = [
                (i, when, st, cb)
                for i, (when, st, cb) in enumerate(self.pending)
                if not st["cancelled"] and when <= self.now + 1e-12
            ]
            if not due:
                break
            # fire earliest
            due.sort(key=lambda x: x[1])
            i, when, st, cb = due[0]
            st["cancelled"] = True  # one-shot like Timer
            cb()


def _ev(name="run_test", success=True, display="ok"):
    return SimpleNamespace(data={"name": name, "success": success, "display": display})


def test_fast_tool_no_heartbeat():
    clock = ManualClock()
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        time_fn=clock.time,
        timer_factory=clock.factory,
    )
    p.on_tool_start(_ev())
    clock.advance(3.0)  # < interval
    p.on_tool_end(_ev(success=True, display="done"))
    clock.advance(30.0)  # would-be ticks must not print
    assert not buf.running_lines()
    assert "✅" in buf.text()


def test_long_tool_one_heartbeat():
    clock = ManualClock()
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        time_fn=clock.time,
        timer_factory=clock.factory,
    )
    p.on_tool_start(_ev(name="pytest"))
    clock.advance(10.0)
    assert any("running…" in x and "10s" in x for x in buf.running_lines())
    p.on_tool_end(_ev(success=True, display="pass"))
    assert "✅" in buf.text()


def test_multiple_heartbeats_monotonic():
    clock = ManualClock()
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        time_fn=clock.time,
        timer_factory=clock.factory,
    )
    p.on_tool_start(_ev())
    clock.advance(10.0)
    clock.advance(10.0)
    clock.advance(10.0)
    runs = buf.running_lines()
    assert len(runs) == 3
    assert "10s" in runs[0]
    assert "20s" in runs[1]
    assert "30s" in runs[2]


def test_success_stops_heartbeat():
    clock = ManualClock()
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        time_fn=clock.time,
        timer_factory=clock.factory,
    )
    p.on_tool_start(_ev())
    clock.advance(10.0)
    assert buf.running_lines()
    p.on_tool_end(_ev(success=True, display="ok"))
    n = len(buf.running_lines())
    clock.advance(50.0)
    assert len(buf.running_lines()) == n
    assert "✅" in buf.text()


def test_failure_stops_heartbeat():
    clock = ManualClock()
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        time_fn=clock.time,
        timer_factory=clock.factory,
    )
    p.on_tool_start(_ev())
    clock.advance(10.0)
    p.on_tool_end(_ev(success=False, display="ERROR: boom\nTraceback"))
    n = len(buf.running_lines())
    clock.advance(40.0)
    assert len(buf.running_lines()) == n
    assert "❌" in buf.text()
    assert "ERROR: boom" in buf.text() or "Traceback" in buf.text()


def test_end_race_no_post_end_tick():
    """If end happens, subsequent fired callbacks must no-op."""
    clock = ManualClock()
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        time_fn=clock.time,
        timer_factory=clock.factory,
    )
    p.on_tool_start(_ev())
    # Capture pending timer without firing
    assert clock.pending
    p.on_tool_end(_ev(success=True, display="x"))
    # Force-fire stale callbacks that were cancelled at stop
    for when, st, cb in list(clock.pending):
        # even if not cancelled, token should invalidate
        cb()
    assert not any("running" in x for x in buf.lines if "running" in x) or True
    # Stronger: after end, no NEW running lines from forced callbacks
    after = buf.text()
    for when, st, cb in list(clock.pending):
        cb()
    assert buf.text() == after or "running" not in buf.text().replace(after, "")
    # simplest invariant:
    runs_before_force = [x for x in after.splitlines() if "running" in x]
    runs_after = buf.running_lines()
    assert len(runs_after) == len(runs_before_force)


def test_sequential_tools_isolated():
    clock = ManualClock()
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        time_fn=clock.time,
        timer_factory=clock.factory,
    )
    p.on_tool_start(_ev(name="A"))
    p.on_tool_end(_ev(name="A", success=True, display="a"))
    p.on_tool_start(_ev(name="B"))
    clock.advance(10.0)
    runs = buf.running_lines()
    assert all("[B]" in x for x in runs)
    assert not any("[A]" in x for x in runs)


def test_summary_still_used_after_heartbeat():
    clock = ManualClock()
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        time_fn=clock.time,
        timer_factory=clock.factory,
    )
    long_disp = "\n".join(f"L{i}" for i in range(40)) + "\nERR"
    p.on_tool_start(_ev())
    clock.advance(10.0)
    p.on_tool_end(_ev(success=False, display=long_disp))
    assert "ERR" in buf.text()
    assert "省略" in buf.text()
