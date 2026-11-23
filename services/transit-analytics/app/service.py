"""Schedule-backed queries and conservative live overlays; no request-time source I/O."""

from datetime import datetime, time, timedelta, timezone
import math
from threading import Lock

from fastapi import HTTPException

from app.models import (ArrivalDeparture, ArrivalsResponse, DelayItem, Disruption, LiveResponse, Occupancy,
    Pagination, Provenance, Route, ScheduleResponse, Stop, TimeEstimate)


def distance_m(lat, lon, stop):
    a, b = math.radians(lat), math.radians(stop.latitude)
    value = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(stop.longitude-lon)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, value))))


def label(data):
    return ("simulated:" + data.source)[:128] if data.simulated else data.source


def page(items, query, *, key, tie):
    items.sort(key=tie)
    items.sort(key=key, reverse=query.sort_order == "desc")
    return items[query.offset:query.offset+query.limit], Pagination(limit=query.limit, offset=query.offset,
        total=len(items), has_more=query.offset + query.limit < len(items))


class TransitService:
    def __init__(self, repository):
        self.repository = repository
        self.index_lock = Lock()
        self.revision, self.index = None, None

    def context(self):
        now = self.repository.clock()
        schedule, live, available = self.repository.snapshots()
        if schedule is None or not schedule.data.valid_from <= now <= schedule.data.valid_through:
            raise HTTPException(503, "No valid published transit schedule is available")
        with self.index_lock:
            if schedule.revision != self.revision:
                data = schedule.data
                routes = {item.route_id: item for item in data.routes}
                stops = {item.stop_id: item for item in data.stops}
                calendars = {item.service_id: item for item in data.calendars}
                trips = {item.trip_id: item for item in data.trips}
                by_stop = {key: [] for key in stops}
                for trip in data.trips:
                    for position, stop_time in enumerate(trip.stop_times):
                        by_stop[stop_time.stop_id].append((trip, stop_time, position))
                self.index = (routes, stops, calendars, trips, by_stop)
                self.revision = schedule.revision
            index = self.index
        if live and (live.data.schedule_version != schedule.data.version or live.schedule_revision != schedule.revision or
                     now - live.received_at > timedelta(seconds=self.repository.retention)):
            live = None
            available = False
        return now, schedule, live, available, index

    def search(self, category, query):
        now, schedule, _, _, (routes, stops, _, _, by_stop) = self.context()
        nearby = []
        matching_routes = set()
        for stop in stops.values():
            distance = distance_m(query.latitude, query.longitude, stop)
            ids = {trip.route_id for trip, _, _ in by_stop[stop.stop_id]}
            if query.mode:
                ids = {key for key in ids if routes[key].mode == query.mode}
            if distance <= query.radius_m and ids:
                nearby.append(Stop(**stop.model_dump(), distance_m=round(distance, 3),
                    modes=sorted({routes[key].mode for key in ids}), route_ids=sorted(ids)))
                matching_routes.update(ids)
        if category == "stops":
            values, pagination = page(nearby, query, key=lambda item: getattr(item, query.sort_by), tie=lambda item: item.stop_id)
            response_type = ScheduleResponse[Stop]
        else:
            values, pagination = page([routes[key] for key in matching_routes], query,
                key=lambda item: getattr(item, query.sort_by), tie=lambda item: item.route_id)
            response_type = ScheduleResponse[Route]
        return response_type(items=values, pagination=pagination, as_of=now, source=label(schedule.data),
            source_updated_at=schedule.data.source_updated_at, schedule_valid_through=schedule.data.valid_through)

    def provenance(self, schedule, live, available, category, now):
        feed = getattr(live.data, category) if live else None
        timestamps = []
        if feed is not None and feed.updated_at is not None:
            timestamps = [feed.updated_at]
            field = "observed_at" if category == "occupancy" else "updated_at"
            timestamps += [getattr(item, field) for item in feed.items]
        stamp = min(timestamps) if timestamps and all(item is not None for item in timestamps) else None
        stale = stamp is not None and (now - stamp).total_seconds() > 60
        state = "stale" if stale else "fresh" if stamp is not None and available else "unavailable"
        actual = state == "fresh" and live is not None and not live.data.simulated and not schedule.data.simulated
        provenance = Provenance(last_live_update_at=stamp,
            source_updated_at=live.data.source_updated_at if live else None,
            service_updated_at=live.received_at if live else None,
            is_live=actual, is_stale=stale, live_update_status=state,
            data_status="simulated" if feed is not None and live.data.simulated else "live" if actual else "cached" if feed is not None else "unavailable",
            source=label(live.data) if live else "unavailable")
        return feed, provenance

    @staticmethod
    def event(seconds, midnight, estimate, use_live):
        if seconds is None:
            return None
        scheduled = midnight + timedelta(seconds=seconds)
        estimated = estimate if use_live and estimate is not None else None
        # The v1 contract exposes whole signed seconds. Fractional predictions
        # cannot satisfy that relationship exactly, so use the known timetable.
        if estimated is not None and (estimated - scheduled).total_seconds() % 1:
            estimated = None
        return TimeEstimate(scheduled_at=scheduled, estimated_at=estimated, display_at=estimated or scheduled,
            basis="live_estimate" if estimated is not None else "schedule",
            delay_seconds=int((estimated-scheduled).total_seconds()) if estimated is not None else None)

    def arrivals(self, stop_id, query):
        now, schedule, live, available, (_, stops, calendars, _, by_stop) = self.context()
        if stop_id not in stops:
            raise HTTPException(404, "Transit stop was not found")
        start = query.start_at or now
        end = start + timedelta(minutes=query.window_minutes)
        if start < now - timedelta(minutes=5) or start > now + timedelta(hours=24):
            raise HTTPException(422, "start_at must be between five minutes ago and 24 hours ahead")
        if start < schedule.data.valid_from or end > schedule.data.valid_through:
            raise HTTPException(422, "Requested interval is outside the published schedule horizon")
        feed, provenance = self.provenance(schedule, live, available, "trips", now)
        updates = {(item.trip_id, item.stop_id, item.service_date): item for item in feed.items} if feed else {}
        candidates = []
        day = start.date() - timedelta(days=1)
        while day <= end.date():
            midnight = datetime.combine(day, time(), tzinfo=timezone.utc)
            active_services = {key for key, calendar in calendars.items() if calendar.operates(day)}
            for trip, stop_time, position in by_stop[stop_id]:
                if query.route_id is not None and query.route_id != trip.route_id or trip.service_id not in active_services:
                    continue
                update = updates.get((trip.trip_id, stop_id, day))
                use_live = provenance.is_live and update is not None
                if use_live and update.cancelled:
                    continue
                # Exclude out-of-window events before constructing/validating
                # response models. A full-day timetable may contain thousands.
                in_window = False
                for name in ("arrival", "departure"):
                    seconds = getattr(stop_time, name + "_seconds")
                    if seconds is None:
                        continue
                    scheduled = midnight + timedelta(seconds=seconds)
                    estimate = getattr(update, name + "_estimated_at") if use_live else None
                    display = estimate if estimate is not None and not (estimate-scheduled).total_seconds() % 1 else scheduled
                    if start <= display < end:
                        in_window = True
                        break
                if not in_window:
                    continue
                arrival = self.event(stop_time.arrival_seconds, midnight, update.arrival_estimated_at if update else None, use_live)
                departure = self.event(stop_time.departure_seconds, midnight, update.departure_estimated_at if update else None, use_live)
                times = [item.display_at for item in (arrival, departure) if item and start <= item.display_at < end]
                if times:
                    row = ArrivalDeparture(trip_id=trip.trip_id, route_id=trip.route_id, stop_id=stop_id,
                        vehicle_id=update.vehicle_id if use_live else None, destination=trip.destination,
                        arrival=arrival, departure=departure)
                    candidates.append((min(times), day, position, row))
            day += timedelta(days=1)
        values, pagination = page(candidates, query, key=lambda item: item[0],
                                 tie=lambda item: (item[3].trip_id, item[1], item[2]))
        rows = [item[3] for item in values]
        uses_estimate = any(event and event.basis == "live_estimate" for row in rows for event in (row.arrival, row.departure))
        if not uses_estimate:
            provenance = provenance.model_copy(update={"is_live": False,
                "data_status": "simulated" if schedule.data.simulated else "scheduled",
                "source": label(schedule.data), "source_updated_at": schedule.data.source_updated_at,
                "service_updated_at": schedule.received_at})
        return ArrivalsResponse(items=rows, pagination=pagination, as_of=now, stop_id=stop_id, provenance=provenance)

    def observations(self, category, query):
        now, schedule, live, available, (_, _, _, trips, _) = self.context()
        feed_category = "trips" if category == "delays" else category
        feed, provenance = self.provenance(schedule, live, available, feed_category, now)
        if feed is None and category == "disruptions":
            raise HTTPException(503, "No trustworthy transit disruption dataset is available")
        rows = []
        if feed is not None and category == "delays":
            for update in feed.items:
                trip = trips[update.trip_id]
                if query.route_id and trip.route_id != query.route_id or query.stop_id and update.stop_id != query.stop_id:
                    continue
                if update.cancelled:
                    continue
                midnight = datetime.combine(update.service_date, time(), tzinfo=timezone.utc)
                for stop_time in trip.stop_times:
                    if stop_time.stop_id != update.stop_id:
                        continue
                    for event_type in ("arrival", "departure"):
                        seconds = getattr(stop_time, event_type + "_seconds")
                        estimate = getattr(update, event_type + "_estimated_at")
                        if seconds is not None and estimate is not None:
                            delay = (estimate - midnight - timedelta(seconds=seconds)).total_seconds()
                            if delay > 0 and delay % 1 == 0:
                                rows.append(DelayItem(trip_id=trip.trip_id, route_id=trip.route_id, stop_id=update.stop_id,
                                    vehicle_id=update.vehicle_id, event_type=event_type, event_at=estimate, delay_seconds=int(delay)))
        elif feed is not None:
            for item in feed.items:
                if category == "disruptions":
                    if query.route_id and query.route_id not in item.route_ids or query.stop_id and query.stop_id not in item.stop_ids:
                        continue
                    if query.status and item.status != query.status or query.kind and item.kind != query.kind:
                        continue
                else:
                    if query.route_id and item.route_id != query.route_id or query.vehicle_id and item.vehicle_id != query.vehicle_id:
                        continue
                rows.append(item)
        tie = (lambda item: item.disruption_id) if category == "disruptions" else (
            (lambda item: (item.trip_id, item.event_type, item.stop_id or "", item.event_at)) if category == "delays" else
            (lambda item: (item.vehicle_id or "", item.route_id)))
        values, pagination = page(rows, query, key=lambda item: getattr(item, query.sort_by), tie=tie)
        coverage = "unavailable" if feed is None else feed.coverage_status if provenance.is_live else "partial"
        response_type = {"delays": LiveResponse[DelayItem], "disruptions": LiveResponse[Disruption], "occupancy": LiveResponse[Occupancy]}[category]
        return response_type(items=values, pagination=pagination, as_of=now, coverage_status=coverage, provenance=provenance)
