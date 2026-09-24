"""Settlement-station registry.

Entries are identifiers observed in Kalshi/NWS rules, not city centroids.  A
registry hit is useful corroboration but never replaces parsing the live rules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WeatherStation:
    city: str
    station_id: str
    climate_product_id: str
    latitude: float
    longitude: float
    timezone: str
    standard_utc_offset_hours: int
    nws_office: str
    source_note: str


# These domestic settlement locations were cross-checked on 2026-09-23 against
# the weather.com/kalshi primary station catalog and NWS station/point metadata.
# New entries require the same source-level verification and tests.
STATIONS: tuple[WeatherStation, ...] = (
    WeatherStation("Atlanta", "KATL", "CLIATL", 33.64028, -84.42694, "America/New_York", -5, "FFC", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Austin", "KAUS", "CLIAUS", 30.18304, -97.67987, "America/Chicago", -6, "EWX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Boston", "KBOS", "CLIBOS", 42.36056, -71.01056, "America/New_York", -5, "BOX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("College Station", "KCLL", "CLICLL", 30.58222, -96.36167, "America/Chicago", -6, "HGX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Columbus", "KCMH", "CLICMH", 39.99070, -82.87691, "America/New_York", -5, "ILN", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Cincinnati / Covington", "KCVG", "CLICVG", 39.04456, -84.67229, "America/New_York", -5, "ILN", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Washington, DC", "KDCA", "CLIDCA", 38.84833, -77.03417, "America/New_York", -5, "LWX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Denver", "KDEN", "CLIDEN", 39.84658, -104.65622, "America/Denver", -7, "BOU", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Dallas", "KDFW", "CLIDFW", 32.89743, -97.02196, "America/Chicago", -6, "FWD", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("West Palm Beach", "KDJT", "CLIDJT", 26.68510, -80.09919, "America/New_York", -5, "MFL", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Newark", "KEWR", "CLIEWR", 40.68250, -74.16944, "America/New_York", -5, "OKX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Gainesville", "KGNV", "CLIGNV", 29.69194, -82.27556, "America/New_York", -5, "JAX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Houston (Hobby)", "KHOU", "CLIHOU", 29.63750, -95.28250, "America/Chicago", -6, "HGX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Houston (Bush Intercontinental)", "KIAH", "CLIIAH", 29.98440, -95.36074, "America/Chicago", -6, "HGX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Jacksonville", "KJAX", "CLIJAX", 30.49534, -81.69370, "America/New_York", -5, "JAX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Las Vegas", "KLAS", "CLILAS", 36.07188, -115.16340, "America/Los_Angeles", -8, "VEF", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Los Angeles", "KLAX", "CLILAX", 33.93806, -118.38889, "America/Los_Angeles", -8, "LOX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Lexington", "KLEX", "CLILEX", 38.03390, -84.61146, "America/New_York", -5, "LMK", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Chicago (Midway)", "KMDW", "CLIMDW", 41.78417, -87.75528, "America/Chicago", -6, "LOT", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Miami", "KMIA", "CLIMIA", 25.79056, -80.31639, "America/New_York", -5, "MFL", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Milwaukee", "KMKE", "CLIMKE", 42.95500, -87.90444, "America/Chicago", -6, "MKX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Minneapolis", "KMSP", "CLIMSP", 44.88306, -93.22889, "America/Chicago", -6, "MPX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("New Orleans", "KMSY", "CLIMSY", 29.99278, -90.25083, "America/Chicago", -6, "LIX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("New York City", "KNYC", "CLINYC", 40.78333, -73.96667, "America/New_York", -5, "OKX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Oklahoma City", "KOKC", "CLIOKC", 35.38861, -97.60028, "America/Chicago", -6, "OUN", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Chicago (O'Hare)", "KORD", "CLIORD", 41.97972, -87.90444, "America/Chicago", -6, "LOT", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Philadelphia", "KPHL", "CLIPHL", 39.87327, -75.22678, "America/New_York", -5, "PHI", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Phoenix", "KPHX", "CLIPHX", 33.42780, -112.00347, "America/Phoenix", -7, "PSR", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Pittsburgh", "KPIT", "CLIPIT", 40.48460, -80.21447, "America/New_York", -5, "PBZ", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Providence", "KPVD", "CLIPVD", 41.72233, -71.42772, "America/New_York", -5, "BOX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("San Diego", "KSAN", "CLISAN", 32.73361, -117.18306, "America/Los_Angeles", -8, "SGX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("San Antonio", "KSAT", "CLISAT", 29.53278, -98.46361, "America/Chicago", -6, "EWX", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Louisville", "KSDF", "CLISDF", 38.17406, -85.73650, "America/Kentucky/Louisville", -5, "LMK", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Seattle", "KSEA", "CLISEA", 47.44472, -122.31361, "America/Los_Angeles", -8, "SEW", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("San Francisco", "KSFO", "CLISFO", 37.61961, -122.36558, "America/Los_Angeles", -8, "MTR", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("San Jose", "KSJC", "CLISJC", 37.35917, -121.92417, "America/Los_Angeles", -8, "MTR", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("St. Petersburg", "KSPG", "CLISPG", 27.76852, -82.62564, "America/New_York", -5, "TBW", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Tampa", "KTPA", "CLITPA", 27.96139, -82.54028, "America/New_York", -5, "TBW", "weather.com/kalshi primary catalog + NWS station metadata"),
    WeatherStation("Trenton", "KTTN", "CLITTN", 40.27639, -74.81639, "America/New_York", -5, "PHI", "weather.com/kalshi primary catalog + NWS station metadata"),
)

_BY_STATION = {station.station_id: station for station in STATIONS}
_BY_CLI = {station.climate_product_id: station for station in STATIONS}


def station_by_identifier(identifier: str | None) -> WeatherStation | None:
    if not identifier:
        return None
    normalized = identifier.upper().strip()
    return _BY_STATION.get(normalized) or _BY_CLI.get(normalized)
