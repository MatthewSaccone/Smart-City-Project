import asyncio
import importlib.util
from pathlib import Path


def test_t040_and_t044_over_real_http():
    path = Path(__file__).resolve().parents[1] / "tools/benchmark.py"
    spec = importlib.util.spec_from_file_location("parking_benchmark", path)
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)

    async def run():
        async with benchmark.local_server() as (url, token):
            return await benchmark.measure(url, token, observation_count=1000, rounds=3)

    result = asyncio.run(run())
    assert result["t040_local_pass"], result
    assert result["t044_local_pass"], result
    assert result["estimation"]["requests"] == 75
    assert result["production_scalability_verified"] is False
