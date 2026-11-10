"""T009 feedback policy. Pure decisions; no Docker/cloud or service mutations."""

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Policy:
    service_id: str = "live-traffic"
    min_replicas: int = 1
    max_replicas: int = 10
    target_rps_per_replica: float = 20
    scale_out_utilization: float = 1.2
    sample_seconds: float = 5
    scale_out_window_seconds: float = 10
    scale_out_cooldown_seconds: float = 15
    scale_in_window_seconds: float = 120
    scale_in_cooldown_seconds: float = 60
    scale_in_utilization: float = 0.4
    latency_target_ms: float = 2000

    def __post_init__(self):
        if not self.service_id:
            raise ValueError("service_id is required")
        if (type(self.min_replicas) is not int or type(self.max_replicas) is not int
                or not 1 <= self.min_replicas <= self.max_replicas):
            raise ValueError("Invalid replica bounds")
        for name in ("target_rps_per_replica", "sample_seconds", "scale_out_window_seconds",
                     "scale_out_cooldown_seconds", "scale_in_window_seconds",
                     "scale_in_cooldown_seconds", "latency_target_ms"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not 0 < self.scale_in_utilization < 1:
            raise ValueError("scale_in_utilization must be between zero and one")
        if not math.isfinite(self.scale_out_utilization) or self.scale_out_utilization < 1:
            raise ValueError("scale_out_utilization must be finite and at least one")

    @classmethod
    def load(cls, path: Path):
        return cls(**json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Metrics:
    measured_at: float
    request_rate: float
    p95_ms: float | None
    error_ratio: float = 0

    def __post_init__(self):
        if not math.isfinite(self.measured_at):
            raise ValueError("measured_at must be finite")
        if not math.isfinite(self.request_rate) or self.request_rate < 0:
            raise ValueError("request_rate must be finite and nonnegative")
        if self.p95_ms is not None and (not math.isfinite(self.p95_ms) or self.p95_ms < 0):
            raise ValueError("p95_ms must be finite and nonnegative")
        if not 0 <= self.error_ratio <= 1:
            raise ValueError("error_ratio must be between zero and one")


@dataclass(frozen=True)
class Decision:
    replicas: int
    reason: str


class Controller:
    def __init__(self, policy: Policy):
        self.policy = policy
        self.high_since = None
        self.low_since = None
        self.last_action = -math.inf
        self.last_sample = None

    def evaluate(self, metrics: Metrics, current: int, now: float,
                 ready: int | None = None) -> Decision:
        p = self.policy
        if not math.isfinite(now) or type(current) is not int or not 0 <= current <= p.max_replicas:
            raise ValueError("Invalid time or current replica count")
        if ready is None:
            ready = current
        if type(ready) is not int or not 0 <= ready <= current:
            raise ValueError("Invalid ready replica count")
        previous = self.last_sample
        if (not 0 <= now - metrics.measured_at <= 2 * p.sample_seconds
                or previous is not None and metrics.measured_at <= previous):
            self.high_since = self.low_since = None
            return Decision(current, "hold: stale, future or repeated metrics")
        self.last_sample = metrics.measured_at
        if previous is not None and metrics.measured_at - previous > 2 * p.sample_seconds:
            self.high_since = self.low_since = None
        if current < p.min_replicas:
            self.last_action = now
            return Decision(p.min_replicas, "restore minimum replicas")
        if ready != current:
            self.high_since = self.low_since = None
            return Decision(current, "hold: instances still becoming ready")

        latency_pressure = (metrics.p95_ms is not None and metrics.p95_ms > p.latency_target_ms
                            and metrics.error_ratio <= 0.1)
        overloaded = (metrics.request_rate > ready * p.target_rps_per_replica * p.scale_out_utilization
                      or latency_pressure)
        low = (metrics.request_rate <= current * p.target_rps_per_replica * p.scale_in_utilization
               and metrics.p95_ms is not None and metrics.p95_ms < p.latency_target_ms / 2
               and metrics.error_ratio == 0)
        if overloaded:
            self.low_since = None
            if self.high_since is None:
                self.high_since = metrics.measured_at
            if (metrics.measured_at - self.high_since >= p.scale_out_window_seconds
                    and now - self.last_action >= p.scale_out_cooldown_seconds):
                desired = min(p.max_replicas, max(current + 1,
                    math.ceil(metrics.request_rate / p.target_rps_per_replica)))
                if desired > current:
                    self.last_action = now
                    self.high_since = None
                    return Decision(desired, "scale out: sustained demand or latency")
                return Decision(current, "hold: maximum replicas reached")
        elif low:
            self.high_since = None
            if self.low_since is None:
                self.low_since = metrics.measured_at
            if (current > p.min_replicas
                    and metrics.measured_at - self.low_since >= p.scale_in_window_seconds
                    and now - self.last_action >= p.scale_in_cooldown_seconds):
                self.last_action = now
                self.low_since = None
                return Decision(current - 1, "scale in: sustained low demand; drain one instance")
        else:
            self.high_since = self.low_since = None
        return Decision(current, "hold: observation window or cooldown")
