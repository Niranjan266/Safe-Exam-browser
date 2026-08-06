"""
In-memory fan-out for live proctoring frames.

The polling design cost two delays that stacked: the candidate captured on one
interval, and the proctor polled on another, so a frame could be up to
capture + poll seconds old before it was seen. This hub removes both — a frame
is handed to every watching proctor the moment it arrives.

Frames are held in memory on purpose. They are worthless a second later, so
writing them to the database just to read them straight back adds latency for
no benefit. The database copy is still written (less often) so the single-frame
view and any serverless fallback keep working.

Scope note: this is per-process state. One worker, or sticky routing, is fine —
which is the normal setup for a lab or a single VM. Across several workers each
would only fan out its own traffic; that needs Redis pub/sub instead, and
`publish()` is the single place that would change.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, Set


class _Channel:
    """Latest frame for one (submission, kind) plus everyone watching it."""

    __slots__ = ("frame", "updated_at", "subscribers", "seq")

    def __init__(self) -> None:
        self.frame: bytes | None = None
        self.updated_at: float = 0.0
        self.seq: int = 0
        self.subscribers: Set["Subscriber"] = set()


class Subscriber:
    """
    A watching proctor. Holds only the newest frame: if a slow client cannot
    keep up we drop the older frame rather than queueing, because stale frames
    are worse than skipped ones for live monitoring.
    """

    __slots__ = ("_event", "_frame", "_lock", "_closed")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._frame: bytes | None = None
        self._lock = threading.Lock()
        self._closed = False

    def offer(self, frame: bytes) -> None:
        with self._lock:
            self._frame = frame          # replaces any frame not yet sent
        self._event.set()

    def wait(self, timeout: float = 20.0) -> bytes | None:
        """Block until a frame arrives. None means the wait timed out."""
        if not self._event.wait(timeout):
            return None
        with self._lock:
            frame, self._frame = self._frame, None
            if self._frame is None:
                self._event.clear()
        return frame

    def close(self) -> None:
        self._closed = True
        self._event.set()

    @property
    def closed(self) -> bool:
        return self._closed


class LiveHub:
    def __init__(self) -> None:
        self._channels: Dict[tuple, _Channel] = defaultdict(_Channel)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    def publish(self, submission_id: int, kind: str, frame: bytes) -> int:
        """Store the newest frame and push it to every watcher immediately."""
        key = (int(submission_id), kind)
        with self._lock:
            ch = self._channels[key]
            ch.frame = frame
            ch.updated_at = time.time()
            ch.seq += 1
            watchers = tuple(ch.subscribers)
            seq = ch.seq
        # Deliver outside the lock: a slow socket must not block the uploader.
        for sub in watchers:
            sub.offer(frame)
        return seq

    def subscribe(self, submission_id: int, kind: str) -> Subscriber:
        sub = Subscriber()
        key = (int(submission_id), kind)
        with self._lock:
            ch = self._channels[key]
            ch.subscribers.add(sub)
            latest = ch.frame
        if latest is not None:
            sub.offer(latest)            # show something at once, don't wait
        return sub

    def unsubscribe(self, submission_id: int, kind: str, sub: Subscriber) -> None:
        key = (int(submission_id), kind)
        with self._lock:
            ch = self._channels.get(key)
            if ch:
                ch.subscribers.discard(sub)
        sub.close()

    # ------------------------------------------------------------------
    def latest(self, submission_id: int, kind: str) -> bytes | None:
        with self._lock:
            ch = self._channels.get((int(submission_id), kind))
            return ch.frame if ch else None

    def age(self, submission_id: int, kind: str) -> float | None:
        """Seconds since the last frame, or None if we have never seen one."""
        with self._lock:
            ch = self._channels.get((int(submission_id), kind))
            if not ch or not ch.updated_at:
                return None
        return time.time() - ch.updated_at

    def has_frame(self, submission_id: int, kind: str) -> bool:
        return self.latest(submission_id, kind) is not None

    def watcher_count(self, submission_id: int, kind: str) -> int:
        with self._lock:
            ch = self._channels.get((int(submission_id), kind))
            return len(ch.subscribers) if ch else 0

    def drop(self, submission_id: int) -> None:
        """Forget an attempt once it ends."""
        with self._lock:
            for kind in ("screen", "cam"):
                ch = self._channels.pop((int(submission_id), kind), None)
                if ch:
                    for sub in ch.subscribers:
                        sub.close()


hub = LiveHub()
