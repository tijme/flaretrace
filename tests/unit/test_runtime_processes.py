"""Exercise coordinated container shutdown, child failures, signals, and startup cleanup."""

import signal
import subprocess
from unittest.mock import Mock

import pytest

from flaretrace.runtime import processes


def test_shutdown_terminates_then_kills_stuck_groups(monkeypatch):
    """Terminate both serving groups, enforce the shared deadline, and reap forced exits.

    Args:
        monkeypatch: Restores mocked process-group signals and monotonic time after use.
    """
    child = Mock(pid=123)
    child.poll.return_value = None
    child.wait.side_effect = [subprocess.TimeoutExpired("server", 10), 0]
    kill = Mock()
    monkeypatch.setattr(processes.os, "killpg", kill)
    processes.stop_processes([child], grace_seconds=0)
    assert kill.call_args_list[0].args == (123, signal.SIGTERM)
    assert kill.call_args_list[1].args == (123, signal.SIGKILL)
    assert child.wait.call_count == 2


def test_shutdown_tolerates_already_exited_groups(monkeypatch):
    """Ignore races where a child exits between polling and process-group termination.

    Args:
        monkeypatch: Restores mocked group-signalling behavior after the test.
    """
    child = Mock(pid=123)
    child.poll.return_value = None
    child.wait.side_effect = [subprocess.TimeoutExpired("server", 0), 0]
    monkeypatch.setattr(processes.os, "killpg", Mock(side_effect=ProcessLookupError))
    processes.stop_processes([child], grace_seconds=0)
    child.poll.return_value = 0
    child.wait.side_effect = None
    processes.stop_processes([child])


@pytest.mark.parametrize("status, expected", [(0, 1), (-9, 1), (23, 23)])
def test_unexpected_service_exit_stops_the_container(monkeypatch, status, expected):
    """Fail the whole container if either required service exits, including a clean unexpected exit.

    Args:
        monkeypatch: Restores mocked child startup and shutdown operations.
        status: Unexpected exit status of the simulated serving process.
        expected: Supervisor's nonzero container exit status.
    """
    child = Mock()
    child.poll.return_value = status
    stopping = Mock()
    stopping.wait.return_value = False
    cleanup = Mock()
    monkeypatch.setattr(processes.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(processes, "stop_processes", cleanup)
    assert processes.supervise([(["server"], {})], stopping) == expected
    cleanup.assert_called_once_with([child])


def test_partial_startup_failure_reaps_started_children(monkeypatch):
    """Stop the first service if launching the second one fails before the supervisor loop.

    Args:
        monkeypatch: Restores mocked process creation, signal handlers, and teardown.
    """
    child = Mock()
    cleanup = Mock()
    monkeypatch.setattr(
        processes.subprocess,
        "Popen",
        Mock(side_effect=[child, OSError("missing proxy")]),
    )
    monkeypatch.setattr(processes, "stop_processes", cleanup)
    with pytest.raises(OSError):
        processes.supervise([(["app"], {}), (["proxy"], {})], Mock())
    cleanup.assert_called_once_with([child])


def test_signals_request_shutdown_and_original_handlers_are_restored(monkeypatch):
    """Translate SIGTERM/SIGINT into graceful shutdown without blocking inside a handler.

    Args:
        monkeypatch: Restores mocked signal registration and process teardown after use.
    """
    handlers = []
    stopping = Mock()
    stopping.wait.side_effect = [False, True]
    child = Mock()
    child.poll.return_value = None

    def register(signum, handler):
        """Capture new handlers and immediately exercise their cancellation behavior.

        Args:
            signum: SIGTERM or SIGINT registration being simulated.
            handler: New callable handler or original handler value being restored.

        Returns:
            Synthetic original handler value to restore after supervisor shutdown.
        """
        handlers.append((signum, handler))
        if callable(handler):
            handler(signum, None)
        return signal.SIG_DFL

    monkeypatch.setattr(processes.signal, "signal", register)
    monkeypatch.setattr(processes.threading, "Event", lambda: stopping)
    monkeypatch.setattr(processes.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(processes, "stop_processes", Mock())
    assert processes.supervise([(["app"], {}), (["proxy"], {})]) == 0
    assert stopping.set.call_count == 2
    assert [handler for signum, handler in handlers[-2:]] == [
        signal.SIG_DFL,
        signal.SIG_DFL,
    ]
