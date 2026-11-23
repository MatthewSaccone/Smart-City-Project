# S5 — Weather Travel Rule Set `weather_travel_rules_v1`

| Property | Value |
|---|---|
| Applies to | `GET /v1/weather/travel-guidance` (S5 FR4, FR5) |
| Implemented in | `services/weather-analytics/app/analytics.py` (`assess`, `risk_level`, `recommendations`) |
| Tests | `services/weather-analytics/tests/test_analytics.py::test_rule_thresholds` |
| Status | Proposed by S5 owner (T052/T053); team review requested |

The S5 contract (§3.5) requires the thresholds behind travel guidance to be written down and testable
before implementation. This is that specification. Changing any value below requires bumping
`rule_set_version` and updating the tests.

Guidance is **informational**: it describes potential weather-related travel difficulty. It is not
an official warning, does not claim a route is safe, and does not claim an official alert was checked.

## 1. Which inputs are used

| `travel_at` | Essential input | Freshness limit | Supplementary input |
|---|---|---|---|
| Within 1 hour of now (or omitted) | Current weather observation | `observed_at` ≤ 30 min old | Air quality (AQHI), if `observed_at` ≤ 60 min old |
| 1 hour to 7 days ahead | Hourly forecast interval containing `travel_at` | `issued_at` ≤ 6 h old | — |

If the essential input is missing, too old, or has no interval covering `travel_at`, the response is
`assessment_status: insufficient_data`, `risk_level: unknown`, `hazards: []`. It is **never** `low`
just because no hazard could be detected. Every input consulted is listed in `inputs[]` with its
reference time and `is_stale` flag. A missing measurement (`null`) never triggers or clears a hazard
by itself.

## 2. Hazard thresholds

| Hazard | Moderate | High |
|---|---|---|
| `reduced_visibility` | visibility < 1000 m, or fog with unknown visibility | visibility < 200 m |
| `heavy_precipitation` | rate ≥ 4 mm/h; or (rate unknown and ≥ 70 % probability of rain/snow/mixed) | rate ≥ 8 mm/h, or thunderstorm |
| `strong_wind` | sustained ≥ 14 m/s (50 km/h) or gust ≥ 20 m/s (72 km/h) | sustained ≥ 20 m/s (72 km/h) or gust ≥ 25 m/s (90 km/h) |
| `icy_conditions` | temperature ≤ 1 °C with rain/snow/mixed or any precipitation | mixed precipitation / freezing rain at ≤ 2 °C (or unknown temperature) |
| `extreme_cold` | feels-like ≤ −27 °C | feels-like ≤ −35 °C |
| `extreme_heat` | feels-like ≥ 31 °C | feels-like ≥ 35 °C |
| `poor_air_quality` | AQHI 7–10 (high risk) | AQHI > 10 (very high risk) |

Feels-like falls back to air temperature when unavailable. For forecasts, the hourly precipitation
amount (mm in that hour) is used as the rate. AQHI uses the Canadian formula on 3-hour mean O₃, NO₂
(ppb) and PM2.5 (µg/m³); if any of the three hours is missing, AQHI is `null` and no air-quality
hazard is derived.

Wind, cold and AQHI thresholds follow the scale of Environment and Climate Change Canada public
criteria (wind/wind-chill warning levels, AQHI risk categories). They are engineering choices for this
project, not ECCC's official alerting rules.

## 3. Risk level and recommendations

- `risk_level` is `high` if any hazard is `high`, `moderate` if any hazard exists, otherwise `low`.
- `recommendations[]` holds one precaution per hazard type (fixed text in `analytics.ADVICE`), followed
  by a disclaimer pointing users to official weather advisories.
