# Settlement Rules

Kalshi's current help material says the source named in each market's rules is
authoritative. Daily contracts historically used the NWS Daily Climate Report,
but current contracts can name The Weather Company; hourly contracts also use
The Weather Company. APEX therefore parses every market independently.

For NWS CLI daily values, the climate day is midnight-to-midnight **local
standard time**. During daylight time that is commonly 1:00 AM through 12:59 AM
wall time the next day. ASOS records rolling five-minute temperatures internally;
hourly or five-minute public METAR values can miss the eventual daily extreme.
Six-hour/24-hour groups and preliminary reports are evidence, not final truth.

`SettlementSpec.tradeable` requires a verified station/CLI identifier, timezone,
date, source, measurement, threshold semantics and observation window with no
ambiguity flags. The parser supports ranges, strict greater/less-than contracts,
and inclusive at-least/at-most contracts. Unknown formats fail closed and remain
available for research/exception review.

The initial registry contains 39 domestic stations cross-checked on 2026-09-23
against weather.com/kalshi's primary station catalog and NWS station/point
metadata. Registry entries corroborate live rules; they never replace them.

Primary references:

- https://help.kalshi.com/en/articles/13823837-weather-markets
- https://docs.kalshi.com/getting_started/quick_start_market_data
- https://www.weather.gov/lox/asostemperature
- https://www.weather.gov/lot/weather_observations_faq
