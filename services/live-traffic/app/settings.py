"""All the numbers the Live Traffic service uses, kept in one place.

If the team agrees on a different value, change it here and nowhere else.
"""

# T018 (S1 QR4): a "live" answer may only use location data from the last 60 seconds.
LIVE_WINDOW_SECONDS = 60

# Older data is kept for 10 minutes as a fallback. It is never called "live".
CACHE_WINDOW_SECONDS = 600

# A location ping whose timestamp is more than this far in the future is rejected.
MAX_FUTURE_SECONDS = 5

# The speed we expect on a city road with no traffic. Congestion is measured against it.
FREE_FLOW_SPEED_KMH = 50.0

# Used when working out delays, so that a stopped car does not give an infinite delay.
MIN_SPEED_KMH = 5.0

# A "speed" above this is treated as a GPS error and ignored.
MAX_REALISTIC_SPEED_KMH = 200.0

# A route is cut into pieces of about this length so each piece gets its own level.
SEGMENT_LENGTH_M = 500.0
MAX_SEGMENTS = 20

# When checking one piece of a route, look for users at least this close to its middle.
MIN_SEARCH_RADIUS_M = 150.0

# Limits on what a user may ask for.
MAX_RADIUS_M = 50000.0
MAX_ROUTE_DISTANCE_M = 50000.0

# ---------- T089: ingestion ----------

# How often the service asks the traffic source for new locations.
POLL_INTERVAL_SECONDS = 5

# How long to wait for the traffic source before giving up on one poll.
POLL_TIMEOUT_SECONDS = 3

# The name of the topic that location updates are published on.
LOCATION_TOPIC = "traffic.locations"
