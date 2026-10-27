"""Supervise the bundled proxy and Gunicorn with coordinated failure and shutdown handling."""

import os
import signal
import subprocess
import sys
import threading
import time


def stop_processes(children, grace_seconds=10):
    """Terminate both process groups and enforce a bounded graceful-shutdown deadline.

    Args:
        children: Started Popen instances, including partially started service sets.
        grace_seconds: Maximum shared shutdown interval before remaining groups are killed.
    """
    deadline = time.monotonic() + grace_seconds
    for child in children:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def supervise(commands, environment, shutdown=None):
    """Exit the container if either service fails and forward termination to both services.

    Args:
        commands: Ordered executable/argument lists for Gunicorn and Caddy.
        environment: Child-process environment containing validated application settings.
        shutdown: Optional Event used by tests; otherwise signal handlers request shutdown.

    Returns:
        Zero after requested shutdown, or a nonzero status after an unexpected service exit.
    """
    stopping = shutdown or threading.Event()
    children = []
    previous_handlers = {}

    def request_shutdown(signum, frame):
        """Record termination without doing blocking work inside a signal handler.

        Args:
            signum: SIGTERM or SIGINT delivered to the supervisor.
            frame: Interrupted Python stack frame; it is not inspected.
        """
        stopping.set()

    try:
        if shutdown is None:
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.signal(signum, request_shutdown)
        for command in commands:
            children.append(
                subprocess.Popen(command, env=environment, start_new_session=True)
            )
        while not stopping.wait(0.2):
            for child in children:
                status = child.poll()
                if status is not None:
                    print(
                        f"A serving process exited unexpectedly (status {status}); stopping the container.",
                        file=sys.stderr,
                        flush=True,
                    )
                    return status if status > 0 else 1
        return 0
    finally:
        stop_processes(children)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
