"""A pretend traffic source for the Live Traffic service to poll (T089).

THIS IS SIMULATED DATA. It stands in for a real traffic data provider.

It is a tiny web server with one URL, /feed, that returns where the eight
simulated cars are right now. The cars are the same ones simulate_users.py uses.

Run it (from the repository root):

    python services/live-traffic/simulate_feed.py --scenario jam

Then start the Live Traffic service with this setting so it polls the feed:

    TRAFFIC_SOURCE_URL=http://127.0.0.1:8091/feed

To demonstrate a source outage, stop this program with Ctrl+C.
To demonstrate recovery, start it again.
"""

import argparse
import threading
import time
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI

from simulate_users import car_position, make_cars, move_car


app = FastAPI(title="Simulated traffic feed (not real data)")

cars = []
last_moved = time.monotonic()
lock = threading.Lock()


@app.get("/feed")
def feed():
    """Move the cars by however long it has been since the last request, then report them."""
    global last_moved

    with lock:
        now = time.monotonic()
        seconds = now - last_moved
        last_moved = now

        locations = []
        for car in cars:
            move_car(car, seconds)
            latitude, longitude = car_position(car)
            locations.append({
                "user_id": car["user_id"],
                "latitude": latitude,
                "longitude": longitude,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    return {"source": "simulated_feed", "locations": locations}


def main():
    parser = argparse.ArgumentParser(description="Serve simulated car locations for polling.")
    parser.add_argument("--scenario", choices=["jam", "clear"], default="jam")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()

    cars.extend(make_cars(args.scenario))

    print("SIMULATED DATA: feed at http://127.0.0.1:" + str(args.port) + "/feed",
          "scenario:", args.scenario)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
