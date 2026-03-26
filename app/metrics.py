"""Metricas simples em memoria para observabilidade do bot."""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime


class _MetricsStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters = defaultdict(int)
        self._latency = {
            "count": 0,
            "sum_ms": 0.0,
            "max_ms": 0.0,
        }
        self._started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    def inc(self, key: str, value: int = 1) -> None:
        if not key:
            return
        with self._lock:
            self._counters[key] += int(value)

    def observe_latency_ms(self, elapsed_ms: float) -> None:
        with self._lock:
            self._latency["count"] += 1
            self._latency["sum_ms"] += float(elapsed_ms)
            if elapsed_ms > self._latency["max_ms"]:
                self._latency["max_ms"] = float(elapsed_ms)

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            lat = dict(self._latency)

        avg = (lat["sum_ms"] / lat["count"]) if lat["count"] else 0.0
        return {
            "started_at": self._started_at,
            "counters": counters,
            "latency_ms": {
                "count": lat["count"],
                "avg": round(avg, 2),
                "max": round(lat["max_ms"], 2),
            },
        }


_store = _MetricsStore()


def inc_counter(key: str, value: int = 1) -> None:
    _store.inc(key, value)


def observe_latency_ms(elapsed_ms: float) -> None:
    _store.observe_latency_ms(elapsed_ms)


def get_metrics_snapshot() -> dict:
    return _store.snapshot()
