"""
Real-time push of notifications to browsers.

PostgreSQL raises NOTIFY 'sras_notifications' with '<user_id>:<id>' whenever a
notification row is committed (migration 007). One background thread LISTENs
on that channel and hands each event to the asyncio queues of that user's
open Server-Sent-Events connections, so a tab learns about a new
notification within milliseconds of the approval being committed.

The browser then fetches the notification itself through the normal,
authenticated endpoint; the push only says "something new for you".
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict

import psycopg

from .config import DATABASE_URL

log = logging.getLogger("realtime")
CHANNEL = "sras_notifications"


class Hub:
    def __init__(self) -> None:
        self._subs: dict[int, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(set)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False

    # -------------------------------------------------------------- subscribers
    def subscribe(self, user_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subs[user_id].add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs[user_id] = {s for s in self._subs[user_id] if s[1] is not q}
            if not self._subs[user_id]:
                del self._subs[user_id]

    def subscriber_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._subs.values())

    def publish(self, user_id: int, notification_id: int) -> None:
        with self._lock:
            targets = list(self._subs.get(user_id, ()))
        for loop, q in targets:
            loop.call_soon_threadsafe(_offer, q, notification_id)

    # -------------------------------------------------------------- listener
    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="pg-listen", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    self.connected, backoff = True, 1
                    log.info("listening for notifications")
                    while not self._stop.is_set():
                        for n in conn.notifies(timeout=1.0):
                            try:
                                uid, nid = (int(x) for x in n.payload.split(":", 1))
                            except ValueError:
                                continue
                            self.publish(uid, nid)
            except Exception as e:  # noqa: BLE001 - keep retrying; polling covers the gap
                self.connected = False
                if self._stop.is_set():
                    break
                log.warning("notification listener lost the database (%s); retrying in %ss", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
        self.connected = False


def _offer(q: asyncio.Queue, item) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:   # a stalled tab: drop, it will catch up on its next fetch
        pass


hub = Hub()
