from datetime import datetime, timezone

import pytest

from src.strategy.monte_carlo import simulate_contract_probability
from src.strategy.settlement import parse_settlement_spec
from src.weather.observations import (
    RawObservation,
    ReportType,
    infer_observation_cadence,
    parse_metar,
)


def _spec():
    return parse_settlement_spec({
        "ticker": "KXHIGHAUS-26SEP23-T96",
        "event_ticker": "KXHIGHAUS-26SEP23",
        "series_ticker": "KXHIGHAUS",
        "title": "Highest temperature in Austin",
        "rules_primary": "Maximum temperature at CLIAUS for Sep 23, 2026 is less than 96 according to The Weather Company.",
    })


def test_metar_tenths_and_six_hour_max_decode():
    raw = RawObservation(
        "KAUS", "aviationweather", ReportType.METAR,
        datetime(2026, 9, 23, 18, 53, tzinfo=timezone.utc),
        datetime(2026, 9, 23, 18, 54, tzinfo=timezone.utc),
        "KAUS 231853Z 18005KT 10SM CLR 35/20 A2992 RMK AO2 SLP100 T03500200 10372",
    )
    obs = parse_metar(raw, "America/Chicago")
    assert obs.temperature_f == 95.0
    assert obs.max_temperature_f == pytest.approx(98.96)
    assert obs.observed_at_local.hour == 13


def test_metar_twenty_four_hour_extremes_take_precedence():
    raw = RawObservation(
        "KAUS", "aviationweather", ReportType.METAR,
        datetime(2026, 9, 23, 23, 53, tzinfo=timezone.utc),
        datetime(2026, 9, 23, 23, 54, tzinfo=timezone.utc),
        "KAUS 232353Z 18005KT 10SM CLR 30/20 A2992 RMK AO2 403720183 10350",
    )
    obs = parse_metar(raw, "America/Chicago")
    assert obs.max_temperature_f == pytest.approx(98.96)
    assert obs.min_temperature_f == pytest.approx(64.94)


def test_specials_do_not_define_routine_cadence():
    base = datetime(2026, 9, 23, tzinfo=timezone.utc)
    observations = [
        RawObservation("KAUS", "x", ReportType.METAR, base.replace(hour=h, minute=53), base, "")
        for h in range(6)
    ] + [RawObservation("KAUS", "x", ReportType.SPECI, base.replace(hour=2, minute=17), base, "")]
    cadence = infer_observation_cadence("KAUS", observations)
    assert cadence.routine_minutes == (53,)
    assert cadence.median_interval_seconds == 3600
    assert cadence.special_observation_count == 1


def test_already_crossed_high_is_logically_impossible():
    result = simulate_contract_probability(
        spec=_spec(), high_so_far_f=97, ensemble_remaining_highs_f=[94, 95], seed=4,
    )
    assert result.probability == 0
    assert result.impossible


def test_monte_carlo_is_seeded_normalized_and_not_raw_certainty():
    a = simulate_contract_probability(
        spec=_spec(), high_so_far_f=90, ensemble_remaining_highs_f=[93, 94, 95],
        simulations=1000, forecast_error_std_f=1, seed=10,
    )
    b = simulate_contract_probability(
        spec=_spec(), high_so_far_f=90, ensemble_remaining_highs_f=[93, 94, 95],
        simulations=1000, forecast_error_std_f=1, seed=10,
    )
    assert a == b
    assert 0 < a.probability < 1
    assert 0 <= a.confidence_low <= a.confidence_high <= 1


def test_already_crossed_daily_low_is_impossible():
    low_spec = parse_settlement_spec({
        "ticker": "KXLOWTAUS-26SEP23-B70",
        "event_ticker": "KXLOWTAUS-26SEP23",
        "series_ticker": "KXLOWTAUS",
        "title": "Lowest temperature in Austin",
        "rules_primary": "Minimum temperature at CLIAUS for Sep 23, 2026 is greater than 70 according to The Weather Company.",
    })
    result = simulate_contract_probability(
        spec=low_spec, high_so_far_f=69, ensemble_remaining_highs_f=[71, 72], seed=2,
    )
    assert result.impossible
    assert result.probability == 0
