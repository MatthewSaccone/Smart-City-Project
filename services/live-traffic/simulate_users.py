"""Pretend to be eight cars so the Live Traffic service has data to analyse.

THIS IS SIMULATED DATA. The cars and the street are made up for demos.

The cars drive back and forth along one straight made-up street in London,
Ontario. Four cars use the south half and four use the north half.

    --scenario jam     south half crawls at 8 km/h, north half moves at 48 km/h
    --scenario clear   every car moves at 48 km/h

Start the service first, then run (from the repository root):

    python services/live-traffic/simulate_users.py --scenario jam
"""

import argparse
import time

import httpx

from app.analytics import distance_m


# The made-up street: three points in a straight line, south to north.
SOUTH = (42.9840, -81.2500)
MIDDLE = (42.9920, -81.2530)
NORTH = (43.0000, -81.2560)

PING_EVERY_SECONDS = 2
SLOW_KMH = 8
FAST_KMH = 48


def make_cars(scenario):
    """Build the list of cars. Each car is a dictionary."""
    if scenario == "jam":
        south_speed = SLOW_KMH
    else:
        south_speed = FAST_KMH

    cars = []

    for number in range(1, 5):
        cars.append({
            "user_id": "sim-car-south-" + str(number),
            "start": SOUTH,
            "end": MIDDLE,
            "speed_kmh": south_speed,
            # Spread the cars out along the street: 20%, 40%, 60%, 80%.
            "fraction": number * 0.2,
            "direction": 1,
        })

    for number in range(1, 5):
        cars.append({
            "user_id": "sim-car-north-" + str(number),
            "start": MIDDLE,
            "end": NORTH,
            "speed_kmh": FAST_KMH,
            "fraction": number * 0.2,
            "direction": 1,
        })

    return cars


def move_car(car, seconds):
    """Move a car forward. When it reaches an end of its street it turns around."""
    street_length_m = distance_m(
        car["start"][0], car["start"][1], car["end"][0], car["end"][1],
    )
    metres = (car["speed_kmh"] / 3.6) * seconds
    car["fraction"] = car["fraction"] + car["direction"] * (metres / street_length_m)

    if car["fraction"] >= 1:
        car["fraction"] = 1
        car["direction"] = -1
    elif car["fraction"] <= 0:
        car["fraction"] = 0
        car["direction"] = 1


def car_position(car):
    """Turn a car's fraction (0 to 1) into a latitude and longitude."""
    latitude = car["start"][0] + (car["end"][0] - car["start"][0]) * car["fraction"]
    longitude = car["start"][1] + (car["end"][1] - car["start"][1]) * car["fraction"]
    return latitude, longitude


def main():
    parser = argparse.ArgumentParser(description="Send simulated car locations to Live Traffic.")
    parser.add_argument("--url", default="http://127.0.0.1:8081")
    parser.add_argument("--scenario", choices=["jam", "clear"], default="jam")
    parser.add_argument("--seconds", type=int, default=120, help="how long to run")
    args = parser.parse_args()

    cars = make_cars(args.scenario)
    rounds = args.seconds // PING_EVERY_SECONDS

    print("SIMULATED DATA: sending", len(cars), "cars to", args.url, "scenario:", args.scenario)

    for round_number in range(1, rounds + 1):
        for car in cars:
            latitude, longitude = car_position(car)
            try:
                response = httpx.post(
                    args.url + "/v1/traffic/locations",
                    json={
                        "user_id": car["user_id"],
                        "latitude": latitude,
                        "longitude": longitude,
                    },
                )
            except httpx.ConnectError:
                print("Could not reach", args.url, "- is the Live Traffic service running?")
                return

            response.raise_for_status()
            move_car(car, PING_EVERY_SECONDS)

        print("round", round_number, "of", rounds, "- sent", len(cars), "pings")
        time.sleep(PING_EVERY_SECONDS)


if __name__ == "__main__":
    main()
