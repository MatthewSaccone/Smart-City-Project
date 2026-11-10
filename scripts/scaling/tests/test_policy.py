import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from policy import Controller, Metrics, Policy
from routing import RegistryRouter
from simulate import simulate


def test_normal_load_stable_and_peak_reaches_ten_after_sustained_window():
    controller = Controller(Policy())
    for t in range(0, 20, 5):
        assert controller.evaluate(Metrics(t, 20, 100), 1, t).replicas == 1
    assert controller.evaluate(Metrics(20, 200, 100), 1, 20).replicas == 1
    assert controller.evaluate(Metrics(25, 200, 100), 1, 25).replicas == 1
    assert controller.evaluate(Metrics(30, 200, 100), 1, 30).replicas == 10


def test_short_spike_does_not_scale_and_metric_gap_resets_evidence():
    controller = Controller(Policy())
    for t, rate in [(0, 200), (5, 20), (10, 200), (30, 200)]:
        assert controller.evaluate(Metrics(t, rate, 100), 1, t).replicas == 1


def test_small_demand_variation_near_normal_load_does_not_scale():
    controller = Controller(Policy())
    for t in range(0, 61, 5):
        assert controller.evaluate(Metrics(t, 22, 100), 1, t).replicas == 1


def test_cooldown_and_pending_readiness_prevent_duplicate_scale_out():
    controller = Controller(Policy())
    for t in (0, 5, 10):
        decision = controller.evaluate(Metrics(t, 40, 100), 1, t)
    assert decision.replicas == 2
    assert controller.evaluate(Metrics(15, 200, 100), 2, 15, ready=1).replicas == 2
    assert controller.evaluate(Metrics(20, 200, 100), 2, 20).replicas == 2
    assert controller.evaluate(Metrics(25, 200, 100), 2, 25).replicas == 2
    assert controller.evaluate(Metrics(30, 200, 100), 2, 30).replicas == 10


def test_scale_in_requires_healthy_low_load_and_removes_only_one_replica():
    controller = Controller(Policy())
    for t in range(0, 120, 5):
        assert controller.evaluate(Metrics(t, 5, 100), 10, t).replicas == 10
    assert controller.evaluate(Metrics(120, 5, 100), 10, 120).replicas == 9
    assert controller.evaluate(Metrics(125, 5, 100), 9, 125).replicas == 9


@pytest.mark.parametrize("p95, errors", [(None, 0), (1500, 0), (100, 0.1)])
def test_missing_latency_slow_requests_or_errors_prevent_scale_in(p95, errors):
    controller = Controller(Policy())
    for t in range(0, 151, 5):
        assert controller.evaluate(Metrics(t, 0, p95, errors), 2, t).replicas == 2


def test_latency_pressure_and_replica_limits():
    controller = Controller(Policy(max_replicas=2))
    for t in (0, 5, 10):
        decision = controller.evaluate(Metrics(t, 1, 2100), 1, t)
    assert decision.replicas == 2
    for t in range(15, 45, 5):
        assert controller.evaluate(Metrics(t, 1000, 2100), 2, t).replicas == 2
    assert Controller(Policy()).evaluate(Metrics(0, 0, None), 0, 0).replicas == 1


def test_dependency_errors_do_not_trigger_latency_only_scale_out():
    controller = Controller(Policy())
    for t in range(0, 31, 5):
        assert controller.evaluate(Metrics(t, 1, 5000, 0.5), 1, t).replicas == 1


def test_stale_future_and_duplicate_metrics_do_not_drive_scaling():
    controller = Controller(Policy())
    for measured, now in [(0, 20), (30, 20), (20, 20), (20, 20)]:
        assert controller.evaluate(Metrics(measured, 200, 100), 1, now).replicas == 1


@pytest.mark.parametrize("changes", [{"min_replicas": 0}, {"max_replicas": 0},
    {"sample_seconds": 0}, {"target_rps_per_replica": float("nan")},
    {"scale_in_utilization": 1}, {"max_replicas": 1.5}])
def test_invalid_configuration(changes):
    with pytest.raises(ValueError):
        Policy(**changes)


@pytest.mark.parametrize("rate, p95, errors", [(-1, 0, 0), (float("inf"), 0, 0),
    (20, float("nan"), 0), (20, 100, 2)])
def test_invalid_metrics(rate, p95, errors):
    with pytest.raises(ValueError):
        Metrics(0, rate, p95, errors)


def test_checked_in_configuration_and_simulation():
    policy = Policy.load(Path(__file__).parents[1] / "policy.json")
    result = simulate(policy)
    assert result["mode"] == "virtual_time_simulation"
    assert result["events"][0]["to"] == 10
    assert result["events"][0]["virtual_seconds"] - 20 <= 30
    assert any(item["to"] < item["from"] for item in result["events"])


NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def instance(name, expired=False):
    return {"instance_id": name, "endpoint": f"http://{name}:8080", "status": "healthy",
            "lease_expires_at": (NOW + timedelta(seconds=-1 if expired else 30)).isoformat()}


def test_round_robin_new_discovery_expiry_and_drain():
    async def run():
        snapshot = [instance("a"), instance("b"), instance("old", expired=True)]
        def handle(request):
            return httpx.Response(200, json={"service_id": "live-traffic", "instances": snapshot})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            router = RegistryRouter(client, "http://registry", "live-traffic")
            await router.refresh()
            assert [router.choose(NOW)["instance_id"] for _ in range(4)] == ["a", "b", "a", "b"]
            router.draining.add("a")
            assert router.choose(NOW)["instance_id"] == "b"
            snapshot.append(instance("c"))
            await router.refresh()
            assert {router.choose(NOW)["instance_id"] for _ in range(4)} == {"b", "c"}
            with pytest.raises(LookupError):
                router.choose(NOW + timedelta(seconds=31))
    asyncio.run(run())


def test_unavailable_discovery_clears_routes_and_failure_retries_another_instance():
    async def run():
        now = datetime.now(timezone.utc)
        calls = []
        def handle(request):
            calls.append(request.url.host)
            return httpx.Response(503 if request.url.host in {"a", "registry"} else 200)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            router = RegistryRouter(client, "http://registry", "live-traffic")
            router.instances = [instance("a"), instance("b")]
            for item in router.instances:
                item["lease_expires_at"] = (now + timedelta(seconds=60)).isoformat()
            selected, response = await router.get("/health")
            assert selected == "b" and response.status_code == 200
            assert calls == ["a", "b"]
            await router.refresh()
            with pytest.raises(LookupError):
                router.choose()
    asyncio.run(run())
