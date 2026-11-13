"""Publish an approved normalized S4 dataset, or explicitly seed simulated demo inputs."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.catalog import load_demo, load_prices
from app.datasets import CatalogFeed, PricingFeed, Tariff, MAX_PAYLOAD_BYTES
from app.models import utc_now
from app.sources import FixtureSource
from app.storage import ParkingDataStore


def demo_feeds():
    facilities, observations, as_of = load_demo()
    prices = load_prices(utc_now())
    return {"catalog": CatalogFeed(source="simulated_fixture", simulated=True, facilities=facilities),
            "pricing": PricingFeed(source="simulated_tariffs", simulated=True, facilities={key:
                Tariff(**{name: getattr(value, name) for name in Tariff.model_fields}) for key, value in prices.items()}),
            "occupancy": FixtureSource(observations, as_of).load(facilities)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-demo", action="store_true")
    parser.add_argument("--stream", choices=["catalog", "pricing", "occupancy"])
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    if args.seed_demo and (args.stream or args.file) or not args.seed_demo and not (args.stream and args.file):
        parser.error("Choose --seed-demo or both --stream and --file")
    database_url = os.getenv("PARKING_DATABASE_URL")
    if not database_url:
        parser.error("Set PARKING_DATABASE_URL for the dedicated S4 database")
    store = ParkingDataStore(database_url)
    store.initialize()
    if args.seed_demo:
        for stream, feed in demo_feeds().items():
            changed = store.publish(stream, feed.model_dump(mode="json"))
            print(f"{stream}: {'updated' if changed else 'unchanged'} (simulated demo)")
    else:
        if args.file.stat().st_size > MAX_PAYLOAD_BYTES:
            parser.error("Dataset exceeds byte limit")
        changed = store.publish(args.stream, json.loads(args.file.read_text(encoding="utf-8")))
        print(f"{args.stream}: {'updated' if changed else 'unchanged'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # No DSNs, paths, credentials or provider payloads in ordinary output.
        print(f"S4 publication failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
