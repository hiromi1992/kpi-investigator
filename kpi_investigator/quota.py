"""Japan-local daily quota contract, thread-safe simulation and Redis Lua adapter plan."""

from __future__ import annotations

import hashlib
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
BROWSER_LIMIT = 3
GLOBAL_LIMIT = 10


def day_key(now: datetime | None = None) -> str:
    return (now or datetime.now(JST)).astimezone(JST).date().isoformat()


def seconds_until_next_jst_midnight(now: datetime | None = None) -> int:
    current = (now or datetime.now(JST)).astimezone(JST)
    next_midnight = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), tzinfo=JST)
    return max(1, int((next_midnight - current).total_seconds()) + 1)


@dataclass
class InMemorySharedState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    by_day: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(dict))
    global_by_day: dict[str, int] = field(default_factory=dict)
    request_ids: set[str] = field(default_factory=set)


class MockAtomicQuota:
    """Thread-atomic mock; does not assert actual Redis durability or correctness."""
    def __init__(self, state: InMemorySharedState | None = None, *, offline: bool = False):
        self.state = state if state is not None else InMemorySharedState()
        self.offline = offline

    def reserve(self, anonymous_browser_id: str, request_id: str, *, now: datetime | None = None) -> str:
        if self.offline:
            raise ConnectionError("Redis unavailable")
        if not anonymous_browser_id or not request_id or len(anonymous_browser_id) > 128 or len(request_id) > 128:
            return "INVALID_ID"
        current_day = day_key(now)
        with self.state.lock:
            if request_id in self.state.request_ids:
                return "DUPLICATE"
            b = self.state.by_day[current_day]
            if b.get(anonymous_browser_id, 0) >= BROWSER_LIMIT:
                return "BROWSER_LIMIT"
            if self.state.global_by_day.get(current_day, 0) >= GLOBAL_LIMIT:
                return "GLOBAL_LIMIT"
            self.state.request_ids.add(request_id)  # Dedup survives different mock instances / day boundaries.
            b[anonymous_browser_id] = b.get(anonymous_browser_id, 0) + 1
            self.state.global_by_day[current_day] = self.state.global_by_day.get(current_day, 0) + 1
            return "RESERVED"

    def counts(self, browser_id: str, *, now: datetime | None = None) -> tuple[int, int]:
        day = day_key(now)
        with self.state.lock:
            return (self.state.by_day[day].get(browser_id, 0), self.state.global_by_day.get(day, 0))


class RedisQuotaContract:
    """Redis adapter requires a supplied atomic EVAL executor. No connection is created."""
    def __init__(self, eval_executor):
        self.eval_executor = eval_executor

    def reserve(self, anonymous_browser_id: str, request_id: str, *, now: datetime | None = None) -> str:
        from pathlib import Path
        if not anonymous_browser_id or not request_id:
            return "INVALID_ID"
        current_day = day_key(now)
        hashed_browser = hashlib.sha256(anonymous_browser_id.encode()).hexdigest()
        hashed_request = hashlib.sha256(request_id.encode()).hexdigest()
        keys = [f"kpi:quota:daily:browser:{current_day}:{hashed_browser}",
                f"kpi:quota:daily:global:{current_day}",
                f"kpi:quota:request:{hashed_request}"]
        lua = (Path(__file__).resolve().parents[1] / "redis" / "reserve.lua").read_text("utf-8")
        try:
            # EVAL evaluates all checks and mutations atomically server-side.
            result = self.eval_executor(lua, keys, [BROWSER_LIMIT, GLOBAL_LIMIT,
                                                    seconds_until_next_jst_midnight(now), 172800])
            return {1: "RESERVED", 0: "DUPLICATE", -1: "BROWSER_LIMIT", -2: "GLOBAL_LIMIT"}.get(int(result), "REDIS_ERROR")
        except Exception:
            return "REDIS_ERROR"
