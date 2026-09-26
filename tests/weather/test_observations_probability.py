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


def test_preliminary_high_crossing_is_not_certainty():
    result = simulate_contract_probability(
        spec=_spec(), high_so_far_f=97, ensemble_remaining_highs_f=[94, 95], seed=4,
    )
    assert 0 < result.probability < 1
    assert not result.impossible


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


def test_preliminary_low_crossing_is_not_certainty():
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
    assert not result.impossible
    assert 0 < result.probability < 1


def test_lax_low_market_replay_uses_observed_minimum_not_afternoon_high():
    # Saved 2026-09-25 evidence: KLAX reached 68F before the NO fill, while
    # the afternoon high was 77F. Generic secondary rules mention both extrema.
    spec = parse_settlement_spec({
        "ticker": "KXLOWTLAX-26SEP25-B68.5",
        "event_ticker": "KXLOWTLAX-26SEP25",
        "title": "Will the minimum temperature be 68-69° on Sep 25, 2026?",
        "rules_primary": ("If the minimum temperature recorded at Los Angeles (CLILAX) "
                          "for Sep 25, 2026, is between 68-69° fahrenheit according "
                          "to The Weather Company, then the market resolves to Yes."),
        "rules_secondary": ("The official and final value is the maximum/minimum "
                            "temperature as reported by the Weather Company."),
    })
    corrected = simulate_contract_probability(
        spec=spec, high_so_far_f=68, ensemble_remaining_highs_f=[70, 72], seed=0,
    )
    assert corrected.probability > 0.5


def test_integer_settlement_rounding_and_invalid_inputs():
    result = simulate_contract_probability(spec=_spec(), high_so_far_f=None,
        ensemble_remaining_highs_f=[95.6], forecast_error_std_f=0,
        observation_error_std_f=0, simulations=100)
    assert result.probability == .05  # publishes 96, so NOT below 96
    for bad in (float('nan'), float('inf')):
        with pytest.raises(ValueError):
            simulate_contract_probability(spec=_spec(), high_so_far_f=bad,
                ensemble_remaining_highs_f=[94])


def test_unbounded_or_cross_day_extrema_cannot_contaminate_market():
    from dataclasses import replace
    from datetime import timedelta
    from src.weather_service import WeatherService
    spec = _spec()
    when = spec.observation_window_start + timedelta(hours=1)
    raw = RawObservation('KAUS', 'aviationweather', ReportType.METAR,
        when, when, 'KAUS 231853Z 35/20 RMK T03500200 10372')
    obs = parse_metar(raw, 'America/Chicago')
    assert WeatherService._extreme_so_far(spec, [obs]) == 95
    overlapping = replace(obs, extreme_window_start=when-timedelta(hours=6),
        extreme_window_end=when)
    assert WeatherService._extreme_so_far(spec, [overlapping]) == 95
    contained = replace(obs, extreme_window_start=spec.observation_window_start,
        extreme_window_end=when)
    assert WeatherService._extreme_so_far(spec, [contained]) == pytest.approx(98.96)
