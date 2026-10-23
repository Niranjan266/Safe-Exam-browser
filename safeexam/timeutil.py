"""Time helpers.

Timestamps are stored as naive UTC. ``datetime.utcnow()`` is deprecated, and an
aware datetime cannot be compared with the naive values read back from the
database, so this returns the same naive-UTC value the old call did.
"""
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)
