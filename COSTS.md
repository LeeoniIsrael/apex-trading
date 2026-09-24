# Costs

## Current monthly estimate (2026-09-23)

For a new Ashburn CPX11, Hetzner's June 2026 price is **$20.49/month** excluding
VAT and IPv4. A Primary IPv4 is **$0.60/month**, for **$21.09/month** before tax.
Free Primary IPv6 alone is possible but complicates administration and API
reachability; the deployment guide assumes IPv4.

- Hetzner CPX11 + IPv4: $21.09/month before tax
- NWS, Aviation Weather, weather.com/kalshi, Open-Meteo: $0 initially
- SQLite/systemd/logrotate: $0
- Runtime AI/Jev: $0 by default; configured caps are $0.10/day and $1/month
- Kalshi fees and slippage: variable, booked per fill

The cost report separates gross trading P&L, fees, slippage, infrastructure/API
costs and final net P&L. Expected monthly income: **INSUFFICIENT DATA**.

References:

- https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/
- https://docs.hetzner.com/cloud/servers/primary-ips/overview/
- https://help.kalshi.com/en/articles/13823805-fees
