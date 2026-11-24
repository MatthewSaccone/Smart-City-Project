"""T047 / S5 QR1: every read returns within 2000 ms, including slow-provider and fallback cases."""

from datetime import timedelta
import time

import pytest

from conftest import NOW, POINT, z

LIMIT = 2.0
READS = [
    ("/v1/weather/current", {}),
    ("/v1/weather/forecast", {"start_at": z(NOW + timedelta(hours=1)), "end_at": z(NOW + timedelta(days=7))}),
    ("/v1/environment/air-quality", {}),
    ("/v1/environment/trends", {"metric": "temperature_c", "start_at": z(NOW - timedelta(days=7)), "end_at": z(NOW)}),
    ("/v1/weather/travel-guidance", {}),
]


def timed(client, path, params):
    start = time.perf_counter()
    response = client.get(path, params={**POINT, **params})
    return response, time.perf_counter() - start


@pytest.mark.parametrize("path, params", READS)
def test_reads_within_2000ms_with_slow_provider(client, provider, path, params):
    provider.delay = 0.3                     # each provider round-trip takes 300 ms
    response, elapsed = timed(client, path, params)
    assert response.status_code == 200 and elapsed < LIMIT, elapsed


@pytest.mark.parametrize("path, params", READS)
def test_fallback_within_2000ms_when_provider_hangs(client, provider, clock, path, params):
    timed(client, path, params)
    provider.mode = {"weather": "hang", "air": "hang"}
    clock.now = NOW + timedelta(minutes=6)
    response, elapsed = timed(client, path, params)
    assert response.status_code == 200 and elapsed < LIMIT, elapsed


def test_cached_reads_are_fast(client):
    timed(client, *READS[0])
    samples = sorted(timed(client, *READS[0])[1] for _ in range(50))
    assert samples[int(len(samples) * 0.95) - 1] < 0.1
