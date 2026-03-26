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
        self._latency_samples: list[float] = []
        self._max_latency_samples = 5000
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
            self._latency_samples.append(float(elapsed_ms))
            if len(self._latency_samples) > self._max_latency_samples:
                self._latency_samples = self._latency_samples[-self._max_latency_samples :]

    @staticmethod
    def _percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        pos = int(round((q / 100.0) * (len(ordered) - 1)))
        return float(ordered[max(0, min(pos, len(ordered) - 1))])

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            lat = dict(self._latency)
            lat_samples = list(self._latency_samples)

        avg = (lat["sum_ms"] / lat["count"]) if lat["count"] else 0.0
        p95 = self._percentile(lat_samples, 95.0)
        total_msgs = int(counters.get("messages.total", 0) or 0)
        fallback_used = int(counters.get("fallback.used", 0) or 0)
        fallback_rate = (fallback_used / total_msgs * 100.0) if total_msgs else 0.0
        erros_por_intencao: dict[str, int] = {}
        for key, val in counters.items():
            if key.startswith("intent_error."):
                erros_por_intencao[key.split(".", 1)[1]] = int(val)

        return {
            "started_at": self._started_at,
            "counters": counters,
            "latency_ms": {
                "count": lat["count"],
                "avg": round(avg, 2),
                "max": round(lat["max_ms"], 2),
                "p95": round(p95, 2),
            },
            "fallback_rate_pct": round(fallback_rate, 2),
            "errors_by_intent": erros_por_intencao,
        }


_store = _MetricsStore()


def inc_counter(key: str, value: int = 1) -> None:
    _store.inc(key, value)


def observe_latency_ms(elapsed_ms: float) -> None:
    _store.observe_latency_ms(elapsed_ms)


def get_metrics_snapshot() -> dict:
    return _store.snapshot()
