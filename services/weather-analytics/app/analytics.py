"""Pure S5 calculations: condition codes, unit conversion, AQHI and travel rules.

Kept free of I/O so each rule is unit-testable. The travel-rule thresholds are the
"separately approved specification" the S5 contract asks for; they are documented
in docs/api/contracts/S5-Travel-Rules-v1.md and must be changed together with it.
"""

from __future__ import annotations

import math
from datetime import datetime

# ------------------------------------------------------------------ condition codes

SEVERITY_RANK = {"unknown": 0, "clear": 1, "cloudy": 2, "windy": 3, "fog": 4, "rain": 5,
                 "snow": 6, "mixed_precipitation": 7, "thunderstorm": 8}


def condition_from_wmo(code: int | None, wind_speed_m_s: float | None = None) -> str:
    """Map a WMO 4677 weather code (as used by Open-Meteo) to the contract enum."""
    if code is None:
        return "unknown"
    if code in (0, 1):
        condition = "clear"
    elif code in (2, 3):
        condition = "cloudy"
    elif code in (45, 48):
        return "fog"
    elif code in (56, 57, 66, 67):
        return "mixed_precipitation"
    elif 51 <= code <= 65 or 80 <= code <= 82:
        return "rain"
    elif 71 <= code <= 77 or code in (85, 86):
        return "snow"
    elif 95 <= code <= 99:
        return "thunderstorm"
    else:
        return "unknown"
    if wind_speed_m_s is not None and wind_speed_m_s >= 12:
        return "windy"
    return condition


def worst_condition(conditions: list[str]) -> str:
    return max(conditions, key=SEVERITY_RANK.__getitem__) if conditions else "unknown"


# ------------------------------------------------------------------ units

MOLAR_VOLUME_L = 24.45  # litres/mol at 25 C and 1 atm, the standard for ug/m3 <-> ppb
MOLAR_MASS = {"o3": 48.00, "no2": 46.01, "so2": 64.07, "co": 28.01}
UNITS = {"pm2_5": "ug_m3", "pm10": "ug_m3", "o3": "ppb", "no2": "ppb", "so2": "ppb", "co": "ppm"}


def to_contract_unit(pollutant: str, ug_m3: float) -> float:
    if UNITS[pollutant] == "ug_m3":
        return round(ug_m3, 2)
    ppb = ug_m3 * MOLAR_VOLUME_L / MOLAR_MASS[pollutant]
    return round(ppb / 1000, 4) if UNITS[pollutant] == "ppm" else round(ppb, 2)


# ------------------------------------------------------------------ AQHI

def aqhi(o3_ppb: float, no2_ppb: float, pm25_ug_m3: float) -> float:
    """Canadian AQHI (Stieb et al. 2008) from 3-hour mean concentrations."""
    raw = (1000 / 10.4) * ((math.exp(0.000537 * o3_ppb) - 1)
                           + (math.exp(0.000871 * no2_ppb) - 1)
                           + (math.exp(0.000487 * pm25_ug_m3) - 1))
    return max(0.0, raw)


def aqhi_category(value: float) -> str:
    rounded = round(value)
    if rounded <= 3:
        return "low"
    if rounded <= 6:
        return "moderate"
    if rounded <= 10:
        return "high"
    return "very_high"


def aqhi_from_hourly(hourly: list[dict], at: datetime) -> float | None:
    """AQHI for the 3 hourly rows ending at ``at``; None unless all 3 hours are complete."""
    window = [row for row in hourly if row["time"] <= at][-3:]
    if len(window) < 3 or (at - window[0]["time"]).total_seconds() > 3 * 3600:
        return None
    if any(row.get(key) is None for row in window for key in ("o3", "no2", "pm2_5")):
        return None
    mean = {key: sum(row[key] for row in window) / 3 for key in ("o3", "no2", "pm2_5")}
    return aqhi(to_contract_unit("o3", mean["o3"]), to_contract_unit("no2", mean["no2"]), mean["pm2_5"])


# ------------------------------------------------------------------ travel rules v1

RULE_SET_VERSION = "weather_travel_rules_v1"
DISCLAIMER = ("This is informational guidance derived from weather data, not an official warning; "
              "check official weather advisories before travelling.")

ADVICE = {
    "reduced_visibility": "Use low-beam headlights, reduce speed and increase following distance.",
    "heavy_precipitation": "Allow extra travel time, reduce speed and watch for pooling water or slush.",
    "strong_wind": "Expect gusts on open roads and bridges; secure loose items and keep both hands on the wheel.",
    "icy_conditions": "Roads, bridges and sidewalks may be icy; slow down and consider delaying non-essential trips.",
    "extreme_heat": "Carry water, avoid strenuous outdoor activity and never leave people or pets in vehicles.",
    "extreme_cold": "Dress in layers, cover exposed skin and carry an emergency kit in your vehicle.",
    "poor_air_quality": "Reduce strenuous outdoor activity; people with heart or lung conditions should take extra care.",
}


def _hazard(kind: str, severity: str, description: str) -> dict:
    return {"hazard_type": kind, "severity": severity, "description": description}


def assess(conditions: dict, aqhi_value: float | None) -> list[dict]:
    """Apply rule set v1 to normalized inputs. Missing (None) inputs never trigger a hazard."""
    hazards: list[dict] = []
    condition = conditions.get("condition_code", "unknown")
    visibility = conditions.get("visibility_m")
    precipitation = conditions.get("precipitation_rate_mm_h")
    probability = conditions.get("precipitation_probability_pct")
    wind = conditions.get("wind_speed_m_s")
    gust = conditions.get("wind_gust_m_s")
    temperature = conditions.get("temperature_c")
    feels = conditions.get("feels_like_c")
    if feels is None:
        feels = temperature

    # Reduced visibility
    if visibility is not None and visibility < 200:
        hazards.append(_hazard("reduced_visibility", "high", f"Visibility is about {visibility:.0f} m."))
    elif visibility is not None and visibility < 1000:
        hazards.append(_hazard("reduced_visibility", "moderate", f"Visibility is about {visibility:.0f} m."))
    elif visibility is None and condition == "fog":
        hazards.append(_hazard("reduced_visibility", "moderate", "Fog may reduce visibility."))

    # Heavy precipitation
    if condition == "thunderstorm":
        hazards.append(_hazard("heavy_precipitation", "high", "Thunderstorms with heavy downpours are possible."))
    elif precipitation is not None and precipitation >= 8:
        hazards.append(_hazard("heavy_precipitation", "high", f"Precipitation of about {precipitation:.1f} mm/h."))
    elif precipitation is not None and precipitation >= 4:
        hazards.append(_hazard("heavy_precipitation", "moderate", f"Precipitation of about {precipitation:.1f} mm/h."))
    elif (precipitation is None and probability is not None and probability >= 70
          and condition in ("rain", "snow", "mixed_precipitation")):
        hazards.append(_hazard("heavy_precipitation", "moderate",
                               f"{probability:.0f}% chance of {condition.replace('_', ' ')}."))

    # Strong wind
    if (wind is not None and wind >= 20) or (gust is not None and gust >= 25):
        hazards.append(_hazard("strong_wind", "high", _wind_text(wind, gust)))
    elif (wind is not None and wind >= 14) or (gust is not None and gust >= 20):
        hazards.append(_hazard("strong_wind", "moderate", _wind_text(wind, gust)))

    # Icy conditions
    wet = condition in ("rain", "snow", "mixed_precipitation") or (precipitation or 0) > 0
    if condition == "mixed_precipitation" and (temperature is None or temperature <= 2):
        hazards.append(_hazard("icy_conditions", "high", "Freezing rain or mixed precipitation may glaze surfaces."))
    elif temperature is not None and temperature <= 1 and wet:
        hazards.append(_hazard("icy_conditions", "moderate",
                               f"Near-freezing temperature ({temperature:.1f} °C) with precipitation."))

    # Temperature extremes (uses feels-like where available)
    if feels is not None and feels <= -35:
        hazards.append(_hazard("extreme_cold", "high", f"Feels like {feels:.0f} °C; frostbite within minutes."))
    elif feels is not None and feels <= -27:
        hazards.append(_hazard("extreme_cold", "moderate", f"Feels like {feels:.0f} °C; risk of frostbite."))
    if feels is not None and feels >= 35:
        hazards.append(_hazard("extreme_heat", "high", f"Feels like {feels:.0f} °C."))
    elif feels is not None and feels >= 31:
        hazards.append(_hazard("extreme_heat", "moderate", f"Feels like {feels:.0f} °C."))

    # Air quality
    if aqhi_value is not None and round(aqhi_value) > 10:
        hazards.append(_hazard("poor_air_quality", "high", f"AQHI is {round(aqhi_value)} (very high risk)."))
    elif aqhi_value is not None and round(aqhi_value) >= 7:
        hazards.append(_hazard("poor_air_quality", "moderate", f"AQHI is {round(aqhi_value)} (high risk)."))
    return hazards


def _wind_text(wind, gust) -> str:
    parts = []
    if wind is not None:
        parts.append(f"winds of about {wind * 3.6:.0f} km/h")
    if gust is not None:
        parts.append(f"gusts to {gust * 3.6:.0f} km/h")
    return ("Strong " + " with ".join(parts) + ".") if parts else "Strong winds."


def risk_level(hazards: list[dict]) -> str:
    if any(h["severity"] == "high" for h in hazards):
        return "high"
    if hazards:
        return "moderate"
    return "low"


def recommendations(hazards: list[dict]) -> list[str]:
    advice = [ADVICE[kind] for kind in dict.fromkeys(h["hazard_type"] for h in hazards)]
    if not hazards:
        advice.append("No weather-related travel hazards were identified from the available data.")
    return advice + [DISCLAIMER]
