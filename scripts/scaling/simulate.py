"""Deterministic virtual-time policy exercise. No performance claims or processes."""

import json
from pathlib import Path

from policy import Controller, Metrics, Policy


def simulate(policy: Policy):
    controller = Controller(policy)
    replicas = policy.min_replicas
    events = []
    # 20 rps baseline, 200 rps peak, then sustained low demand for scale-in.
    for tick in range(int(300 / policy.sample_seconds) + 1):
        seconds = tick * policy.sample_seconds
        rate = 20 if seconds < 20 else 200 if seconds < 65 else 5
        decision = controller.evaluate(Metrics(seconds, rate, 100), replicas, seconds)
        if decision.replicas != replicas:
            events.append({"virtual_seconds": seconds, "rps": rate, "from": replicas,
                           "to": decision.replicas, "reason": decision.reason})
            replicas = decision.replicas
    return {"mode": "virtual_time_simulation", "latency_ms": "synthetic; not measured",
            "assumption": "actuation and readiness immediate", "events": events}


if __name__ == "__main__":
    print(json.dumps(simulate(Policy.load(Path(__file__).with_name("policy.json"))), indent=2))
