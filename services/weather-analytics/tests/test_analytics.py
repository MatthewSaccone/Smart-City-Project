"""Unit tests for conversions, AQHI and travel rule set v1 thresholds."""

import pytest

from app import analytics


def test_unit_conversions():
    assert analytics.to_contract_unit("pm2_5", 12.345) == 12.35
    assert analytics.to_contract_unit("o3", 48.0) == 24.45      # 1 ppb O3 = 1.963 ug/m3
    assert analytics.to_contract_unit("co", 1145.6) == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize("value, category", [(0.4, "low"), (3.4, "low"), (3.6, "moderate"), (6.4, "moderate"),
                                             (7, "high"), (10.4, "high"), (10.6, "very_high")])
def test_aqhi_categories(value, category):
    assert analytics.aqhi_category(value) == category


def test_aqhi_formula_reference_value():
    # 30 ppb O3, 20 ppb NO2, 10 ug/m3 PM2.5 -> 96.15 * (0.01624 + 0.01757 + 0.00488) = 3.72.
    assert analytics.aqhi(30, 20, 10) == pytest.approx(3.72, abs=0.01)


@pytest.mark.parametrize("code, wind, expected", [
    (0, None, "clear"), (3, 2, "cloudy"), (3, 13, "windy"), (45, None, "fog"), (63, None, "rain"),
    (67, None, "mixed_precipitation"), (75, None, "snow"), (95, None, "thunderstorm"), (None, None, "unknown"),
    (42, None, "unknown")])
def test_wmo_mapping(code, wind, expected):
    assert analytics.condition_from_wmo(code, wind) == expected


def rules(**conditions):
    return {h["hazard_type"]: h["severity"] for h in analytics.assess({"condition_code": "clear", **conditions}, None)}


def test_rule_thresholds():
    assert rules(visibility_m=999) == {"reduced_visibility": "moderate"}
    assert rules(visibility_m=1000) == {}
    assert rules(condition_code="fog") == {"reduced_visibility": "moderate"}
    assert rules(precipitation_rate_mm_h=4) == {"heavy_precipitation": "moderate"}
    assert rules(precipitation_rate_mm_h=8, temperature_c=5) == {"heavy_precipitation": "high"}
    assert rules(condition_code="snow", precipitation_probability_pct=80, temperature_c=-3) == {
        "heavy_precipitation": "moderate", "icy_conditions": "moderate"}
    assert rules(wind_speed_m_s=14) == {"strong_wind": "moderate"}
    assert rules(wind_gust_m_s=25) == {"strong_wind": "high"}
    assert rules(temperature_c=-20, feels_like_c=-30) == {"extreme_cold": "moderate"}
    assert rules(temperature_c=-30, feels_like_c=-38) == {"extreme_cold": "high"}
    assert rules(temperature_c=33, feels_like_c=36) == {"extreme_heat": "high"}
    assert rules(temperature_c=1, condition_code="cloudy") == {}     # cold but dry
    assert analytics.assess({}, 7.2) == [{"hazard_type": "poor_air_quality", "severity": "moderate",
                                           "description": "AQHI is 7 (high risk)."}]


def test_missing_inputs_never_create_hazards_and_risk_levels():
    assert analytics.assess({"condition_code": "unknown"}, None) == []
    assert analytics.risk_level([]) == "low"
    assert analytics.risk_level([{"severity": "moderate"}, {"severity": "high"}]) == "high"
