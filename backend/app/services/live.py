"""Live attendance events for the web console (Server-Sent Events over PostgreSQL LISTEN/NOTIFY).

``publish`` runs inside the caller's transaction: PostgreSQL delivers the NOTIFY only when that
transaction commits, so a rolled-back check-in never shows up on a dashboard. One listener thread per
backend process holds a single LISTEN connection and fans events out to every connected browser, so
any number of open consoles costs one database connection. Payloads carry names and ids only, never
images, embeddings or scores.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from typing import Any

import psycopg
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.base import utcnow

logger = logging.getLogger("app.live")

CHANNEL = "attendance_events"
QUEUE_SIZE = 100


def publish(db: Session, kind: str, **fields: Any) -> None:
    """Queue an event on the current transaction (delivered on commit, dropped on rollback)."""
    payload = json.dumps({"kind": kind, "at": utcnow().isoformat(), **fields}, default=str)
    db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload})


def libpq_url(sqlalchemy_url: str) -> str:
    """``postgresql+psycopg://...`` -> ``postgresql://...`` (psycopg takes a plain libpq URL)."""
    scheme, sep, rest = sqlalchemy_url.partition("://")
    return f"{scheme.split('+', 1)[0]}{sep}{rest}"


class Broadcaster:
    """Process-wide LISTEN connection that forwards notifications to asyncio subscribers.

    The listener thread starts with the first subscriber and exits (closing its connection) once the
    last one leaves. psycopg's async connection does not run on Windows' default event loop, so the
    blocking LISTEN lives in a thread and hands payloads to the loop with ``call_soon_threadsafe``.
    """

    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue[str], asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._listener: threading.Thread | None = None  # the thread whose LISTEN is active, if any
        self._stop = threading.Event()
        self._url: str | None = None

    def configure(self, database_url: str) -> None:
        self._url = libpq_url(database_url)
        self._stop.clear()

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subscribers[queue] = asyncio.get_running_loop()
            if not self._running:
                self._running = True
                self._thread = threading.Thread(target=self._run, name="live-attendance", daemon=True)
                self._thread.start()
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)

    def stop(self) -> None:
        self._stop.set()

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._listener is not None

    def _deliver(self, payload: str) -> None:
        with self._lock:
            subscribers = list(self._subscribers.items())
        for queue, loop in subscribers:
            with contextlib.suppress(RuntimeError):  # that loop has already closed
                loop.call_soon_threadsafe(_offer, queue, payload)

    def _should_exit(self) -> bool:
        """Called with no lock held; flips ``_running`` atomically with the emptiness check."""
        with self._lock:
            if self._stop.is_set() or not self._subscribers:
                self._running = False
                return True
            return False

    def _run(self) -> None:
        backoff = 1.0
        try:
            while not self._should_exit():
                try:
                    if self._url is None:
                        raise RuntimeError("live events are not configured")
                    with psycopg.connect(self._url, autocommit=True, connect_timeout=10) as conn:
                        conn.execute(f"LISTEN {CHANNEL}")
                        self._set_listener(threading.current_thread())
                        backoff = 1.0
                        try:
                            while not self._should_exit():
                                for note in conn.notifies(timeout=2.0):
                                    self._deliver(note.payload)
                        finally:
                            self._set_listener(None)
                        return
                except Exception as exc:  # keep listening across database restarts
                    logger.warning("live event listener error; retrying", extra={"error": type(exc).__name__})
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 30.0)
        finally:
            with self._lock:  # only if this thread is still the listener (dies on an unexpected error)
                if self._thread is threading.current_thread():
                    self._running = False

    def _set_listener(self, thread: threading.Thread | None) -> None:
        with self._lock:
            if thread is not None or self._listener is threading.current_thread():
                self._listener = thread


def _offer(queue: asyncio.Queue[str], payload: str) -> None:
    if queue.full():  # a stalled browser must not hold events for the others; it refetches on reconnect
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    queue.put_nowait(payload)


broadcaster = Broadcaster()
