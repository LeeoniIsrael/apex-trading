from datetime import date, datetime, timezone

import pytest

from src.strategy.settlement import (
    MarketType,
    OfficialSource,
    parse_settlement_spec,
    value_in_contract,
)
from src.weather.stations import STATIONS, station_by_identifier


def _market(**overrides):
    market = {
        "ticker": "KXHIGHAUS-26SEP23-T96",
        "event_ticker": "KXHIGHAUS-26SEP23",
        "series_ticker": "KXHIGHAUS",
        "title": "Highest temperature in Austin today?",
        "rules_primary": (
            "Resolves Yes if the maximum temperature recorded at Austin "
            "(CLIAUS) for Sep 23, 2026, is less than 96 degrees fahrenheit "
            "according to The Weather Company."
        ),
        "close_time": "2026-09-24T04:59:00Z",
        "expected_expiration_time": "2026-09-24T12:00:00Z",
    }
    market.update(overrides)
    return market


def test_current_twc_daily_rule_parses_and_uses_local_wall_day():
    spec = parse_settlement_spec(_market())
    assert spec.official_source == OfficialSource.WEATHER_COMPANY
    assert spec.station_id == "KAUS"
    assert spec.market_date == date(2026, 9, 23)
    assert spec.threshold_high == 96
    assert spec.inclusive_high is False
    assert spec.observation_window_start == datetime(2026, 9, 23, 5, tzinfo=timezone.utc)
    assert spec.observation_window_end == datetime(2026, 9, 24, 5, tzinfo=timezone.utc)
    assert spec.tradeable


def test_nws_daily_window_remains_local_standard_time_during_dst():
    spec = parse_settlement_spec(_market(
        rules_primary=(
            "Resolves Yes if the maximum temperature recorded at Austin-Bergstrom "
            "(CLIAUS) for Sep 23, 2026 is less than 96 degrees according to the "
            "National Weather Service Climatological Report (Daily)."
        )
    ))
    assert spec.official_source == OfficialSource.NWS_CLI
    # Austin standard time is UTC-6 even though Sep 23 is in daylight time.
    assert spec.observation_window_start == datetime(2026, 9, 23, 6, tzinfo=timezone.utc)
    assert spec.observation_window_end == datetime(2026, 9, 24, 6, tzinfo=timezone.utc)


def test_conflicting_sources_fail_closed():
    spec = parse_settlement_spec(_market(
        rules_secondary="Outcome also verified from NWS Daily Climate Report."
    ))
    assert "conflicting_official_sources" in spec.ambiguity_flags
    assert not spec.tradeable


def test_unknown_station_fails_closed():
    spec = parse_settlement_spec(_market(
        rules_primary="Maximum temperature at KZZZ is less than 96 according to The Weather Company."
    ))
    assert "station_unresolved" in spec.ambiguity_flags
    assert not spec.tradeable


@pytest.mark.parametrize("value,expected", [(95.0, True), (96.0, False), (97.0, False)])
def test_exclusive_upper_threshold(value, expected):
    assert value_in_contract(value, parse_settlement_spec(_market())) is expected


def test_hourly_requires_exact_observation_time():
    spec = parse_settlement_spec(_market(
        title="Austin temperature hourly",
        rules_primary="Hourly temperature at KAUS according to The Weather Company is greater than 80.",
    ))
    assert "hourly_observation_time_missing" in spec.ambiguity_flags
    assert not spec.tradeable


def test_live_greater_than_strike_is_exclusive():
    spec = parse_settlement_spec(_market(
        ticker="KXHIGHAUS-26SEP23-T95",
        floor_strike=95,
        cap_strike=None,
        rules_primary="Maximum temperature at Austin (CLIAUS) for Sep 23, 2026 is greater than 95 degrees according to The Weather Company.",
    ))
    assert spec.threshold_low == 95
    assert spec.inclusive_low is False
    assert not value_in_contract(95, spec)
    assert value_in_contract(96, spec)


def test_inclusive_range_contract():
    spec = parse_settlement_spec(_market(
        ticker="KXHIGHAUS-26SEP23-B90.5",
        rules_primary=(
            "Maximum temperature at Austin (CLIAUS) for Sep 23, 2026 is "
            "90 to 94 degrees according to The Weather Company."
        ),
    ))
    assert spec.threshold_low == 90
    assert spec.threshold_high == 94
    assert value_in_contract(90, spec)
    assert value_in_contract(94, spec)
    assert not value_in_contract(89, spec)


def test_hourly_market_uses_local_wall_clock_and_dst():
    spec = parse_settlement_spec(_market(
        title="Austin temperature hourly",
        rules_primary=(
            "Temperature at KAUS at 3:00 pm on Sep 23, 2026 is greater than 90 "
            "according to The Weather Company."
        ),
    ))
    assert spec.market_type == MarketType.HOURLY
    assert spec.observation_window_start == datetime(2026, 9, 23, 20, tzinfo=timezone.utc)
    assert spec.observation_window_end == datetime(2026, 9, 23, 20, 1, tzinfo=timezone.utc)


def test_station_registry_identifiers_are_unique_and_bidirectional():
    assert len(STATIONS) == 39
    assert len({station.station_id for station in STATIONS}) == len(STATIONS)
    assert len({station.climate_product_id for station in STATIONS}) == len(STATIONS)
    for station in STATIONS:
        assert station_by_identifier(station.station_id) == station
        assert station_by_identifier(station.climate_product_id) == station


def test_conflicting_station_date_and_unknown_inclusivity_are_blocked():
    assert not parse_settlement_spec(_market(rules_secondary='Station KLAX')).tradeable
    assert not parse_settlement_spec(_market(event_ticker='KXHIGHAUS-26SEP24')).tradeable
    assert not parse_settlement_spec(_market(rules_primary='Maximum temperature at CLIAUS for Sep 23, 2026 according to The Weather Company.',floor_strike=90)).tradeable
    spec=parse_settlement_spec(_market(rules_primary='Hourly temperature at KAUS at 3:00 pm on Sep 23, 2026 is greater than 90 according to The Weather Company.'))
    assert 'hourly_execution_not_supported' in spec.ambiguity_flags


def test_between_range_semantics():
    spec=parse_settlement_spec(_market(rules_primary='Maximum temperature at CLIAUS for Sep 23, 2026 is between 78 and 79 degrees according to The Weather Company.'))
    assert spec.tradeable and spec.threshold_low==78 and spec.threshold_high==79
