from datetime import date

from src.weather.climate_reports import parse_cli_product
from src.weather.metar import MetarProvider
from src.weather.open_meteo import OpenMeteoEnsembleProvider
from src.weather.observations import ReportType


def test_aviationweather_parser_distinguishes_speci():
    rows = MetarProvider.parse("KAUS", [{
        "rawOb": "SPECI KAUS 231917Z 18005KT 10SM CLR 35/20 A2992",
        "obsTime": "2026-09-23T19:17:00Z",
    }])
    assert len(rows) == 1
    assert rows[0].report_type == ReportType.SPECI


def test_open_meteo_member_parser():
    run = OpenMeteoEnsembleProvider.parse("KAUS", {
        "hourly": {
            "time": ["2026-09-23T18:00", "2026-09-23T19:00"],
            "temperature_2m_member01": [90.0, 91.0],
            "temperature_2m_member02": [89.0, None],
        }
    })
    assert run.member_count == 2
    assert [len(member) for member in run.members] == [2, 1]
    assert run.members[0][1].temperature_f == 91


def test_cli_daily_extremes_parser():
    text = """
CLIAUS
...THE AUSTIN CLIMATE SUMMARY FOR SEPTEMBER 22 2026...
 TEMPERATURE (F)
  YESTERDAY
   MAXIMUM         100
   MINIMUM          71
"""
    report = parse_cli_product("id", __import__("datetime").datetime.fromisoformat("2026-09-23T08:00:00+00:00"), text)
    assert report.report_date == date(2026, 9, 22)
    assert report.maximum_f == 100
    assert report.minimum_f == 71

