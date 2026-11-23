import asyncio
import importlib.util
import json
from pathlib import Path


def test_fifty_concurrent_arrivals_and_mixed_requests_over_real_http():
    path = Path(__file__).resolve().parents[1] / "tools/benchmark.py"
    spec = importlib.util.spec_from_file_location("s6_benchmark", path)
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    async def run():
        async with benchmark.local_server() as url:
            return await benchmark.measure(url, rounds=1)
    report = asyncio.run(run())
    assert report["t055_local_pass"] and report["t061_local_pass"], json.dumps(report, indent=2)
    for name in ("concurrent_arrivals", "concurrent_mixed"):
        assert report[name]["requests"] == report[name]["successful_responses"] == 50
        assert report[name]["peak_in_flight"] == 50
        assert report[name]["errors"] == []
