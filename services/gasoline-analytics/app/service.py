"""Geodesic search, report freshness and CAD/L comparison for S7."""
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.models import to_iso

EARTH_RADIUS_M = 6371008.8
FRESHNESS = timedelta(hours=24)


def distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lon = math.radians((lon2 - lon1 + 180) % 360 - 180)
    h = (math.sin(d_lat / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2)**2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, h)))


def bounds(lat, lon, radius):
    lat_delta = math.degrees(radius / EARTH_RADIUS_M)
    min_lat, max_lat = max(-90, lat-lat_delta), min(90, lat+lat_delta)
    if min_lat <= -90 or max_lat >= 90:
        return min_lat, max_lat, -180, 180
    lon_delta = math.degrees(math.asin(min(1, math.sin(radius/EARTH_RADIUS_M)/math.cos(math.radians(lat)))))
    if lon-lon_delta < -180 or lon+lon_delta > 180:
        return min_lat, max_lat, -180, 180
    return min_lat, max_lat, lon-lon_delta, lon+lon_delta


def is_stale(iso_time, now):
    return now - datetime.fromisoformat(iso_time.replace('Z', '+00:00')) > FRESHNESS


def quote_from_row(row, now):
    basis = 'observed_at' if row['observed_at'] else 'submitted_at'
    return {
        'fuel_type': row['fuel_type'],
        'price_per_litre': float(Decimal(row['price_millis']) / 1000),
        'currency': 'CAD', 'price_unit': 'CAD/L',
        'price_report_id': row['price_report_id'],
        'submitted_at': row['submitted_at'], 'observed_at': row['observed_at'],
        'freshness_basis': basis, 'verification_status': row['verification_status'],
        'source': 'user_report', 'data_status': 'user_report', 'is_live': False,
        'is_stale': is_stale(row[basis], now),
    }


def make_stations(rows, now, include_stale=True, fuel_type=None):
    stations = {}
    all_quotes = {}
    for row in rows:
        sid = row['station_id']
        if sid not in stations:
            grades = json.loads(row['available_fuel_types'])
            stations[sid] = {
                'station_id': sid, 'name': row['name'], 'address': row['address'],
                'latitude': row['latitude'], 'longitude': row['longitude'],
                'distance_m': None, 'available_fuel_types': grades,
                'prices': [], 'last_accepted_report_at': row['last_accepted_report_at'],
                'price_coverage_status': 'unavailable', 'is_stale': False,
            }
            all_quotes[sid] = []
        if row['price_report_id'] is not None:
            all_quotes[sid].append(quote_from_row(row, now))
    for sid, station in stations.items():
        quotes = [q for q in all_quotes[sid] if fuel_type is None or q['fuel_type'] == fuel_type]
        shown = [q for q in quotes if include_stale or not q['is_stale']]
        station['prices'] = shown
        fresh = sum(not q['is_stale'] for q in quotes)
        if fresh:
            expected = 1 if fuel_type else len(station['available_fuel_types'])
            station['price_coverage_status'] = 'available' if fresh >= expected else 'partial'
        elif quotes:
            station['price_coverage_status'] = 'stale_only'
        station['is_stale'] = any(q['is_stale'] for q in shown)
    return list(stations.values())


def nearby(db, lat, lon, radius, now, include_stale, fuel_type=None):
    rows = db.get_station_rows(bounds=bounds(lat, lon, radius))
    stations = make_stations(rows, now, include_stale, fuel_type)
    result = []
    for station in stations:
        distance = distance_m(lat, lon, station['latitude'], station['longitude'])
        if distance <= radius + 1e-7:  # Include stations exactly at the radius boundary.
            station['distance_m'] = round(distance, 3)
            result.append(station)
    return result


def price_for(station, fuel_type):
    return next((q for q in station['prices'] if q['fuel_type'] == fuel_type), None)


def sort_stations(items, sort_by, sort_order, fuel_type=None):
    """Stable deterministic station_id tie-breaker, missing quotes always last."""
    items.sort(key=lambda item: item['station_id'])
    descending = sort_order == 'desc'
    if sort_by == 'name':
        items.sort(key=lambda item: item['name'].casefold(), reverse=descending)
    elif sort_by in ('distance_m',):
        items.sort(key=lambda item: item['distance_m'], reverse=descending)
    else:
        # No quote stays at the end, even for descending order.
        items.sort(key=lambda item: (
            price_for(item, fuel_type) is None,
            0 if price_for(item, fuel_type) is None else (
                Decimal(str(price_for(item, fuel_type)['price_per_litre']))
                if sort_by == 'price_per_litre' else
                datetime.fromisoformat(price_for(item, fuel_type)['submitted_at'].replace('Z', '+00:00')).timestamp()
            ) * (-1 if descending else 1),
        ))
    return items


def pagination(items, limit, offset):
    total = len(items)
    return items[offset:offset+limit], {'limit':limit,'offset':offset,'total':total,'has_more':offset+limit < total}


def comparison_summary(items):
    n = len(items)
    if n < 2:
        return {'eligible_station_count':n,'min_price_per_litre':None,'max_price_per_litre':None,
                'mean_price_per_litre':None,'spread_per_litre':None,'comparison_status':'insufficient_data'}
    amounts = [Decimal(str(item['quote']['price_per_litre'])) for item in items]
    minimum, maximum = min(amounts), max(amounts)
    average = (sum(amounts) / n).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
    return {'eligible_station_count':n, 'min_price_per_litre':float(minimum),
            'max_price_per_litre':float(maximum), 'mean_price_per_litre':float(average),
            'spread_per_litre':float(maximum-minimum), 'comparison_status':'available'}


def response_data_status(stations):
    return 'user_report' if any(item['prices'] for item in stations) else 'unavailable'
