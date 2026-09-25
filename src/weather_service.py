"""One autonomous, fail-closed weather research/paper-trading cycle."""

from __future__ import annotations

import json
import statistics
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from src.execution.paper_executor import PaperExecutor
from src.execution.live_executor import LiveExecutor
from src.execution.order_manager import cancel_stale_paper_orders
from src.execution.position_manager import evaluate_exit
from src.kalshi.auth import KalshiSigner
from src.kalshi.fees import trading_fee_usd
from src.kalshi.client import KalshiClientV2
from src.kalshi.orderbook import parse_orderbook
from src.monitoring.costs import CostTracker
from src.research.collector import MarketCollector
from src.research.accounting import paper_account
from src.research.experiments import MODEL_VERSION, record_candidate
from src.research.settlements import SettlementReconciler
from src.risk.exposure import RiskPolicy, RiskState, risk_blocks
from src.risk.sizing import SizingLimits, size_contracts
from src.storage.database import Database
from src.strategy.decision_engine import Decision, DecisionAction, decide, apply_portfolio_blocks
from src.strategy.edge import calculate_edge
from src.strategy.opportunities import ranked_opportunities
from src.strategy.jev import JevClient, JevReview
from src.strategy.monte_carlo import simulate_contract_probability
from src.strategy.runtime_review import RuntimeExceptionReviewer
from src.strategy.settlement import OfficialSource, SettlementSpec, parse_settlement_spec
from src.weather.forecasts import ForecastRun
from src.weather.metar import MetarProvider
from src.weather.nws import NWSProvider
from src.weather.observations import NormalizedObservation, parse_metar
from src.weather.open_meteo import OpenMeteoEnsembleProvider
from src.weather.stations import STATIONS, WeatherStation, station_by_identifier
from src.weather.weather_company import WeatherCompanyProvider
from src.weather_config import WeatherSettings


class WeatherService:
    def __init__(self, settings: WeatherSettings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.migrate()
        if settings.monthly_vps_cost_usd:
            month = datetime.now(timezone.utc).strftime("%Y-%m")
            CostTracker(self.database).record(
                "infrastructure", settings.monthly_vps_cost_usd,
                "Configured monthly VPS and IPv4 cost", f"vps-{month}",
            )
        self.client = KalshiClientV2(base_url=settings.kalshi_base_url)
        self.cost_tracker = CostTracker(self.database)
        self.jev = (
            JevClient(
                api_key=settings.jev_api_key,
                cost_tracker=self.cost_tracker,
                daily_budget_usd=settings.max_ai_daily_usd,
                monthly_budget_usd=settings.max_ai_monthly_usd,
                endpoint=settings.jev_endpoint,
                model=settings.jev_model,
            ) if settings.jev_enabled else None
        )
        self.exception_reviewer = (
            RuntimeExceptionReviewer(
                api_key=settings.openai_api_key,
                cost_tracker=self.cost_tracker,
                daily_budget_usd=settings.max_ai_daily_usd,
                monthly_budget_usd=settings.max_ai_monthly_usd,
                model=settings.runtime_llm_model,
            ) if settings.runtime_llm_enabled else None
        )
        self.paper = PaperExecutor()
        self.live: LiveExecutor | None = None
        if settings.trading_mode == "live":
            if not settings.kalshi_private_key_path:
                raise ValueError("KALSHI_PRIVATE_KEY_PATH is required in live mode")
            authenticated = KalshiClientV2(
                base_url=settings.kalshi_base_url,
                signer=KalshiSigner(settings.kalshi_api_key_id,
                                    settings.kalshi_private_key_path),
            )
            live_database = Database(settings.live_database_path)
            live_database.migrate()
            self.live = LiveExecutor(settings, authenticated, live_database)
            self.live.reconcile()
        self.twc = WeatherCompanyProvider()
        self.metar = MetarProvider()
        self.ensemble = OpenMeteoEnsembleProvider()
        self.nws = NWSProvider(settings.nws_user_agent)
        self._spec_cache: tuple[datetime, list[SettlementSpec]] | None = None
        self._observation_cache: dict[tuple[str, OfficialSource], tuple[datetime, list[NormalizedObservation]]] = {}
        self._forecast_cache: dict[str, tuple[datetime, ForecastRun]] = {}
        self._nws_forecast_cache: dict[str, tuple[datetime, ForecastRun]] = {}
        self._jev_cache: dict[str, tuple[datetime, JevReview]] = {}
        self._cadence_minutes: set[int] = set()
        self._last_settlement_reconcile: datetime | None = None

    def _record_health(self, severity: str, component: str, code: str, message: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO health_events(occurred_at,severity,component,code,message,details_json) "
                "VALUES(?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), severity, component, code, message, "{}"),
            )

    def _control_blocked(self) -> bool:
        with self.database.connect() as connection:
            states = dict(connection.execute(
                "SELECT key,value FROM control_state WHERE key IN ('paused','emergency_stop')"
            ).fetchall())
        return states.get("paused") == "true" or states.get("emergency_stop") == "true"

    def _station_data(
        self, station: WeatherStation, source: OfficialSource,
    ) -> tuple[list[NormalizedObservation], ForecastRun, ForecastRun] | None:
        try:
            now = datetime.now(timezone.utc)
            key = (station.station_id, source)
            cached_observations = self._observation_cache.get(key)
            if cached_observations and now - cached_observations[0] < timedelta(seconds=30):
                observations = cached_observations[1]
            elif source == OfficialSource.WEATHER_COMPANY:
                observations = self.twc.hourly(station)
                self._observation_cache[key] = (now, observations)
            else:
                observations = [parse_metar(raw, station.timezone)
                                for raw in self.metar.fetch(station.station_id, hours=30)]
                self._observation_cache[key] = (now, observations)
            minute_counts = Counter(item.observed_at_utc.minute for item in observations)
            if minute_counts:
                threshold = max(2, int(len(observations) * 0.20))
                self._cadence_minutes.update(
                    minute for minute, count in minute_counts.items() if count >= threshold
                )
            cached_forecast = self._forecast_cache.get(station.station_id)
            if cached_forecast and now - cached_forecast[0] < timedelta(hours=3):
                forecast = cached_forecast[1]
            else:
                forecast = self.ensemble.fetch(station)
                self._forecast_cache[station.station_id] = (now, forecast)
            cached_nws = self._nws_forecast_cache.get(station.station_id)
            if cached_nws and now - cached_nws[0] < timedelta(hours=1):
                nws_forecast = cached_nws[1]
            else:
                nws_forecast = self.nws.fetch_hourly_forecast(station)
                self._nws_forecast_cache[station.station_id] = (now, nws_forecast)
            return observations, forecast, nws_forecast
        except Exception as exc:
            self._record_health("error", "weather_provider", "fetch_failed",
                                f"{station.station_id}: {type(exc).__name__}")
            return None

    @staticmethod
    def _extreme_so_far(spec: SettlementSpec, observations: list[NormalizedObservation]) -> float | None:
        values: list[float] = []
        is_low = bool(spec.measurement and spec.measurement.value == "low")
        for observation in observations:
            if not spec.observation_window_start or not spec.observation_window_end:
                continue
            if spec.observation_window_start <= observation.observed_at_utc < spec.observation_window_end:
                if observation.temperature_f is not None:
                    values.append(observation.temperature_f)
                # A report timestamp does not establish when its 6h/24h extreme
                # occurred. Preserve unbounded remarks for research, not trading.
                if (observation.extreme_window_start is not None
                    and observation.extreme_window_end is not None
                    and spec.observation_window_start <= observation.extreme_window_start
                    < observation.extreme_window_end <= spec.observation_window_end):
                    extreme = observation.min_temperature_f if is_low else observation.max_temperature_f
                    if extreme is not None:
                        values.append(extreme)
        if not values:
            return None
        return min(values) if spec.measurement and spec.measurement.value == "low" else max(values)

    @staticmethod
    def _remaining_extremes(spec: SettlementSpec, forecast: ForecastRun, now: datetime) -> list[float]:
        highs: list[float] = []
        for member in forecast.members:
            values = [point.temperature_f for point in member
                      if spec.observation_window_start and spec.observation_window_end
                      and max(now, spec.observation_window_start) <= point.valid_at_utc < spec.observation_window_end]
            if values:
                highs.append(min(values) if spec.measurement and spec.measurement.value == "low" else max(values))
        return highs

    def _persist_inputs(
        self,
        station: WeatherStation,
        observations: list[NormalizedObservation],
        forecasts: tuple[ForecastRun, ...],
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO weather_stations(station_id,climate_product_id,city,latitude,longitude," 
                "timezone,standard_utc_offset_hours,source_note) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(station_id) DO UPDATE SET source_note=excluded.source_note",
                (station.station_id, station.climate_product_id, station.city,
                 station.latitude, station.longitude, station.timezone,
                 station.standard_utc_offset_hours, station.source_note),
            )
            for observation in observations:
                connection.execute(
                    "INSERT OR IGNORE INTO weather_observations(station_id,source,report_type," 
                    "observed_at_utc,observed_at_local,ingested_at_utc,raw_payload,temperature_f," 
                    "max_temperature_f,min_temperature_f,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (observation.station_id, observation.source, observation.report_type.value,
                     observation.observed_at_utc.isoformat(), observation.observed_at_local.isoformat(),
                     observation.ingested_at_utc.isoformat(), observation.raw_payload,
                     observation.temperature_f, observation.max_temperature_f,
                     observation.min_temperature_f, observation.status.value),
                )
            for forecast in forecasts:
                raw = json.dumps(asdict(forecast), default=str)
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO forecast_runs(station_id,provider,model,generated_at_utc,"
                    "ingested_at_utc,raw_json) VALUES(?,?,?,?,?,?)",
                    (forecast.station_id, forecast.provider, forecast.model,
                     forecast.generated_at_utc.isoformat(), forecast.ingested_at_utc.isoformat(), raw),
                )
                if cursor.rowcount:
                    run_id = cursor.lastrowid
                    for member_index, member in enumerate(forecast.members):
                        for point in member:
                            connection.execute(
                                "INSERT INTO weather_forecasts(forecast_run_id,member,valid_at_utc,"
                                "temperature_f,features_json) VALUES(?,?,?,?,?)",
                                (run_id, member_index, point.valid_at_utc.isoformat(),
                                 point.temperature_f, json.dumps(asdict(point), default=str)),
                            )

    def run_once(self) -> dict[str, int]:
        if self.live is not None:
            try:
                self.live.reconcile()
            except Exception:
                with self.live.database.transaction() as c:
                    c.execute("INSERT INTO health_events(occurred_at,severity,component,code,message,details_json) VALUES(?,?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(),'critical','live','portfolio_reconciliation_failed','New orders blocked; account reconciliation required','{}'))
                raise RuntimeError('live account reconciliation blocked') from None
        if self.settings.monthly_vps_cost_usd:
            month=datetime.now(timezone.utc).strftime('%Y-%m')
            self.cost_tracker.record('infrastructure',self.settings.monthly_vps_cost_usd,
                                     'Configured monthly VPS and IPv4 cost',f'vps-{month}')
        if self._control_blocked():
            return {"markets": 0, "current_tradeable_markets": 0,
                    "predictions": 0, "decisions": 0, "paper_orders": 0}
        cancel_stale_paper_orders(self.database)
        reconcile_now = datetime.now(timezone.utc)
        if (self._last_settlement_reconcile is None
                or reconcile_now - self._last_settlement_reconcile >= timedelta(minutes=10)):
            try:
                SettlementReconciler(
                    self.database, nws_user_agent=self.settings.nws_user_agent,
                    twc=self.twc,
                ).reconcile()
            except Exception as exc:
                self._record_health("warning", "settlement", "settlement_reconcile_failed",
                                    type(exc).__name__)
            self._last_settlement_reconcile = reconcile_now
        now = datetime.now(timezone.utc)
        if self._spec_cache and now - self._spec_cache[0] < timedelta(minutes=10):
            specs = self._spec_cache[1]
        else:
            specs = MarketCollector(self.client, self.database).discover()
            self._spec_cache = (now, specs)
        current_specs = [spec for spec in specs if spec.tradeable
                         and spec.observation_window_start and spec.observation_window_end
                         and spec.last_trading_time and now < spec.last_trading_time
                         and spec.observation_window_start <= now < spec.observation_window_end]
        station_cache: dict[
            tuple[str, OfficialSource], tuple[list[NormalizedObservation], ForecastRun, ForecastRun]
        ] = {}
        station_requests: dict[tuple[str, OfficialSource], WeatherStation] = {}
        for spec in current_specs:
            station = station_by_identifier(spec.station_id or spec.nws_climate_product_id)
            if station is not None and spec.official_source is not None:
                station_requests[(station.station_id, spec.official_source)] = station
        with ThreadPoolExecutor(max_workers=6) as pool:
            pending = {
                pool.submit(self._station_data, station, key[1]): (key, station)
                for key, station in station_requests.items()
            }
            for future in as_completed(pending):
                key, station = pending[future]
                fetched = future.result()
                if fetched is None:
                    continue
                station_cache[key] = fetched
                observations_fetched, ensemble_fetched, nws_fetched = fetched
                self._persist_inputs(
                    station, observations_fetched, (ensemble_fetched, nws_fetched),
                )
        with self.database.connect() as connection:
            position_rows = connection.execute(
                "SELECT p.ticker,p.contracts,p.average_price_cents,p.realized_pnl_usd,p.updated_at,"
                "m.raw_json FROM positions p LEFT JOIN markets m ON m.ticker=p.ticker"
            ).fetchall()
            resting_rows = connection.execute(
                "SELECT o.ticker,o.contracts,o.filled_contracts,o.price_cents,m.raw_json "
                "FROM orders o LEFT JOIN markets m ON m.ticker=o.ticker "
                "WHERE o.action='buy' AND o.status IN "
                "('resting','partially_filled','submitting','unknown')"
            ).fetchall()
        total_exposure = sum(
            float(row["contracts"]) * float(row["average_price_cents"]) / 100
            for row in position_rows if row["contracts"] > 0
        ) + sum(
            max(0, int(row["contracts"]) - int(row["filled_contracts"]))
            * int(row["price_cents"]) / 100 for row in resting_rows
        )
        open_tickers = {row["ticker"] for row in position_rows if row["contracts"] > 0}
        open_tickers.update(row["ticker"] for row in resting_rows)
        city_exposure: dict[str, float] = {}
        for row in position_rows:
            if row["contracts"] <= 0 or not row["raw_json"]:
                continue
            position_spec = parse_settlement_spec(json.loads(row["raw_json"]))
            city = position_spec.city or "unknown"
            city_exposure[city] = city_exposure.get(city, 0.0) + (
                row["contracts"] * row["average_price_cents"] / 100
            )
        for row in resting_rows:
            if not row["raw_json"]:
                continue
            order_spec = parse_settlement_spec(json.loads(row["raw_json"]))
            city = order_spec.city or "unknown"
            reserved = max(0, row["contracts"] - row["filled_contracts"]) * row["price_cents"] / 100
            city_exposure[city] = city_exposure.get(city, 0.0) + reserved
        today_prefix = now.date().isoformat()
        daily_pnl = sum(
            float(row["realized_pnl_usd"]) for row in position_rows
            if str(row["updated_at"]).startswith(today_prefix)
        )
        account = paper_account(self.database, self.settings.bankroll, now=now)
        drawdown = account.max_drawdown
        daily_pnl = account.daily_pnl
        with self.database.transaction() as connection:
            connection.execute("INSERT OR REPLACE INTO equity_history VALUES(?,?,?)",
                               (now.isoformat(), account.equity, drawdown))
        if account.discrepancies:
            self._record_health("error", "accounting", "ledger_discrepancy", ",".join(account.discrepancies))
            return {"markets": len(specs), "predictions": 0, "decisions": 0, "paper_orders": 0}
        predictions = decisions = paper_orders = 0

        for spec in current_specs:
            station = station_by_identifier(spec.station_id or spec.nws_climate_product_id)
            assert station is not None and spec.official_source is not None
            key = (station.station_id, spec.official_source)
            if key not in station_cache:
                continue
            observations, forecast, nws_forecast = station_cache[key]
            observed_extreme = self._extreme_so_far(spec, observations)
            ensemble_extremes = self._remaining_extremes(spec, forecast, now)
            nws_extremes = self._remaining_extremes(spec, nws_forecast, now)
            remaining = ensemble_extremes + nws_extremes
            forecast_disagreement = (
                abs(statistics.median(ensemble_extremes) - statistics.median(nws_extremes))
                if ensemble_extremes and nws_extremes else 0.0
            )
            data_conflict = forecast_disagreement > self.settings.max_forecast_disagreement_f
            if observed_extreme is None or not remaining:
                self._record_health("warning", "model", "insufficient_weather_data", spec.ticker)
                continue
            estimate = simulate_contract_probability(
                spec=spec, high_so_far_f=observed_extreme,
                ensemble_remaining_highs_f=remaining,
                simulations=self.settings.monte_carlo_simulations,
            )
            predictions += 1
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO model_predictions(ticker,predicted_at,model_version,probability," 
                    "confidence_low,confidence_high,inputs_json,high_so_far_f) VALUES(?,?,?,?,?,?,?,?)",
                    (spec.ticker, now.isoformat(), MODEL_VERSION, estimate.probability,
                     estimate.confidence_low, estimate.confidence_high,
                     json.dumps({"members": len(remaining), "uncertainty_f": estimate.model_uncertainty_f}),
                     observed_extreme),
                )

            try:
                raw_book = self.client.get_orderbook(spec.ticker, depth=100)
                book = parse_orderbook(raw_book)
            except Exception as exc:
                self._record_health("warning", "kalshi", "orderbook_unavailable",
                                    f"{spec.ticker}: {type(exc).__name__}")
                continue
            with self.database.connect() as connection:
                market_row = connection.execute(
                    "SELECT raw_json FROM markets WHERE ticker=?", (spec.ticker,)
                ).fetchone()
            market_metadata = json.loads(market_row[0]) if market_row else {}
            if market_metadata.get("_fee_type") != "quadratic":
                self._record_health("warning", "fees", "unknown_fee_schedule", spec.ticker)
                continue
            try:
                fee_rate = Decimal("0.07") * Decimal(str(market_metadata["_fee_multiplier"]))
            except (KeyError, ValueError, InvalidOperation):
                self._record_health("warning", "fees", "invalid_fee_multiplier", spec.ticker)
                continue
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO orderbook_snapshots(ticker,captured_at,orderbook_json) VALUES(?,?,?)",
                    (spec.ticker, now.isoformat(), json.dumps(raw_book)),
                )

            with self.database.transaction() as connection:
                for held_side in ('yes', 'no'):
                    bid_now=book.best_bid(held_side)
                    if bid_now is not None:
                        connection.execute("UPDATE research_candidates SET min_bid_cents=MIN(COALESCE(min_bid_cents,?),?) WHERE ticker=? AND side=?", (bid_now,bid_now,spec.ticker,held_side))
                already_settled=connection.execute("SELECT 1 FROM settlements WHERE ticker=? AND final=1",(spec.ticker,)).fetchone()
            if already_settled:
                continue
            # Research uses a fixed capped budget even when the real portfolio is blocked.
            # Final portfolio checks still control all orders below.
            choices = ranked_opportunities(
                probability_yes=estimate.probability, orderbook=book,
                budget_usd=min(2.0, self.settings.bankroll*.02),
                fee_rate=fee_rate, min_liquidity=self.settings.min_liquidity)
            if not choices:
                continue
            edge=choices[0]
            side=edge.side
            side_probability=edge.model_probability
            best_ask=book.best_ask(side)
            contracts=edge.contracts
            exposure = contracts * edge.executable_price_cents / 100 + edge.fee_usd
            blocks = risk_blocks(
                RiskState(
                    len(open_tickers), total_exposure,
                    city_exposure.get(spec.city or "unknown", 0.0),
                    daily_pnl, drawdown,
                ),
                RiskPolicy(bankroll_usd=max(.01, min(self.settings.bankroll, account.equity))), exposure,
            )
            confidence = max(0.0, 1.0 - estimate.entropy_bits)
            external_vetoes: tuple[str, ...] = ()
            if (
                self.exception_reviewer is not None
                and not blocks
                and edge.net_ev_usd > 0
                and forecast_disagreement >= self.settings.llm_review_disagreement_f
            ):
                try:
                    exception_result = self.exception_reviewer.review({
                        "ticker": spec.ticker,
                        "settlement_source": spec.official_source.value,
                        "settlement_parse_confidence": spec.parse_confidence,
                        "ensemble_vs_nws_extreme_disagreement_f": round(forecast_disagreement, 2),
                        "observation_age_seconds": round(
                            (now - max(item.observed_at_utc for item in observations)).total_seconds()
                        ),
                        "model_confidence": round(confidence, 4),
                        "hard_data_conflict": data_conflict,
                        "deterministic_risk_blocks": list(blocks),
                    })
                    external_vetoes = exception_result.review.veto_codes
                except Exception as exc:
                    external_vetoes = ("exception_reviewer_unavailable",)
                    self._record_health(
                        "warning", "runtime_llm", "review_failed", type(exc).__name__,
                    )
            decision = decide(
                settlement=spec, edge=edge,
                data_stale=(not 0 <= (now - max(item.observed_at_utc for item in observations)).total_seconds() <= 7200
                            or forecast.is_stale(now, timedelta(hours=8))
                            or nws_forecast.is_stale(now, timedelta(hours=3))),
                data_conflict=data_conflict,
                model_confidence=confidence,
                min_model_confidence=self.settings.min_model_confidence,
                min_net_edge=self.settings.min_net_edge,
                max_spread_cents=self.settings.max_spread_cents,
                min_liquidity_contracts=1,
                risk_reason_codes=(),
                external_veto_codes=(),
            )
            deterministic_action = decision.action.value
            # Shadow qualification never authorizes an order or an AI call.
            decision = apply_portfolio_blocks(decision, blocks + external_vetoes)
            jev_action: str | None = None
            jev_latency_ms = 0.0
            jev_cost_usd = 0.0
            if (
                self.jev is not None
                and decision.action in {DecisionAction.BUY_YES, DecisionAction.BUY_NO}
                and edge.gross_edge < self.settings.jev_obvious_edge
            ):
                latest_observation = max(item.observed_at_utc for item in observations)
                jev_cache_key = "|".join((
                    spec.ticker, side, str(best_ask), latest_observation.isoformat(),
                    forecast.generated_at_utc.isoformat(), nws_forecast.generated_at_utc.isoformat(),
                ))
                try:
                    cached_review = self._jev_cache.get(jev_cache_key)
                    if cached_review and now - cached_review[0] < timedelta(minutes=5):
                        review = cached_review[1]
                    else:
                        review = self.jev.review({
                            "ticker": spec.ticker,
                            "side": side,
                            "official_source": spec.official_source.value,
                            "observation_age_seconds": round(
                                (now - latest_observation).total_seconds()
                            ),
                            "forecast_age_seconds": round(
                                (now - forecast.generated_at_utc).total_seconds()
                            ),
                            "model_probability": round(side_probability, 4),
                            "model_confidence": round(confidence, 4),
                            "executable_price_cents": edge.executable_price_cents,
                            "gross_edge": edge.gross_edge,
                            "net_ev_usd": edge.net_ev_usd,
                            "spread_cents": edge.spread_cents,
                            "fillable_contracts": edge.fillable_contracts,
                            "contracts": contracts,
                            "risk_checks": "passed",
                        })
                        self._jev_cache[jev_cache_key] = (now, review)
                    jev_action = review.decision.decision
                    jev_latency_ms = review.latency_ms
                    jev_cost_usd = review.cost_usd
                    vetoes = review.decision.veto_codes(self.settings.jev_min_confidence)
                except Exception as exc:
                    vetoes = ("jev_unavailable",)
                    jev_action = "ERROR"
                    self._record_health("warning", "jev", "review_failed", type(exc).__name__)
                if vetoes:
                    decision = Decision(DecisionAction.SKIP, tuple(sorted(set(vetoes))), edge)
            decisions += 1
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO decisions(ticker,decided_at,action,reason_codes,edge_json) "
                    "VALUES(?,?,?,?,?)", (spec.ticker, now.isoformat(), decision.action.value,
                    json.dumps(decision.reason_codes), json.dumps(asdict(edge))),
                )
                connection.execute(
                    "INSERT INTO decision_benchmarks(ticker,decided_at,deterministic_action,"
                    "jev_action,final_action,latency_ms,api_cost_usd,entry_price_cents,"
                    "net_ev_usd,fill_probability,details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (spec.ticker, now.isoformat(), deterministic_action, jev_action,
                     decision.action.value, jev_latency_ms, jev_cost_usd,
                     edge.executable_price_cents, edge.net_ev_usd, edge.fill_probability,
                     json.dumps({"jev_bypassed_obvious": self.jev is not None
                                 and edge.gross_edge >= self.settings.jev_obvious_edge})),
                )
            latest_observation = max(item.observed_at_utc for item in observations)
            latest_temperature = next((item.temperature_f for item in sorted(observations, key=lambda x:x.observed_at_utc, reverse=True) if item.temperature_f is not None), None)
            forecast_points = [point for member in forecast.members for point in member]
            predicted_temperature = (min(forecast_points, key=lambda p:abs((p.valid_at_utc-latest_observation).total_seconds())).temperature_f if forecast_points else None)
            with self.database.connect() as connection:
                prior = connection.execute("SELECT probability,price_cents,observation_age,captured_at FROM research_candidates WHERE ticker=? AND side=? ORDER BY id DESC LIMIT 1", (spec.ticker, side)).fetchone()
            new_observation = prior and latest_observation > datetime.fromisoformat(prior['captured_at'])-timedelta(seconds=prior['observation_age'])
            control = record_candidate(
                self.database, spec=spec, now=now, side=side, edge=edge,
                baseline=deterministic_action, final=decision.action.value, jev=jev_action,
                observation_age=(now-latest_observation).total_seconds(),
                surprise=latest_temperature-predicted_temperature if latest_temperature is not None and predicted_temperature is not None else None,
                disagreement=forecast_disagreement,
                probability_change=side_probability-prior['probability'] if new_observation else None,
                book_change=(edge.executable_price_cents-prior['price_cents'])/100 if new_observation else None,
                latest_bid=book.best_bid(side))
            if control:
                continue
            if decision.action.value not in {"BUY_YES", "BUY_NO"}:
                continue
            if self._control_blocked():
                break
            client_order_id = str(uuid.uuid4())
            if self.live is not None:
                self.live.submit_buy(
                    ticker=spec.ticker, side=side, price_cents=best_ask,
                    contracts=contracts, client_order_id=client_order_id,
                )
                total_exposure += exposure
                city = spec.city or "unknown"
                city_exposure[city] = city_exposure.get(city, 0.0) + exposure
                open_tickers.add(spec.ticker)
                paper_orders += 1
                continue
            fill = self.paper.submit_buy(
                order_key=client_order_id, side=side, limit_price_cents=best_ask,
                contracts=contracts, orderbook=book,
            )
            paper_orders += 1
            total_exposure += exposure
            city = spec.city or "unknown"
            city_exposure[city] = city_exposure.get(city, 0.0) + exposure
            open_tickers.add(spec.ticker)
            order_id = f"PAPER-{client_order_id}"
            with self.database.transaction() as connection:
                connection.execute("UPDATE research_candidates SET order_id=? WHERE ticker=? AND captured_at=?", (order_id, spec.ticker, now.isoformat()))
                connection.execute(
                    "INSERT INTO orders(id,client_order_id,ticker,side,action,price_cents,contracts," 
                    "filled_contracts,status,paper,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (order_id, client_order_id, spec.ticker, side, "buy", best_ask,
                     contracts, fill.filled_contracts, fill.status.value, 1,
                     now.isoformat(), now.isoformat()),
                )
                if fill.filled_contracts:
                    fill_id = f"FILL-{client_order_id}"
                    fill_price = round(fill.average_fill_price_cents or best_ask)
                    fee = float(trading_fee_usd(fill.filled_contracts, fill_price, rate=fee_rate))
                    connection.execute(
                        "INSERT INTO fills(id,order_id,ticker,side,contracts,price_cents,fee_usd,filled_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (fill_id, order_id, spec.ticker, side, fill.filled_contracts,
                         fill_price, fee, now.isoformat()),
                    )
                    connection.execute(
                        "INSERT INTO positions(ticker,side,contracts,average_price_cents," 
                        "realized_pnl_usd,updated_at) VALUES(?,?,?,?,0,?) "
                        "ON CONFLICT(ticker,side) DO UPDATE SET "
                        "average_price_cents=((positions.average_price_cents*positions.contracts)+" 
                        "(excluded.average_price_cents*excluded.contracts))/(positions.contracts+excluded.contracts)," 
                        "contracts=positions.contracts+excluded.contracts,updated_at=excluded.updated_at",
                        (spec.ticker, side, fill.filled_contracts, fill_price, now.isoformat()),
                    )
        return {
            "markets": len(specs), "current_tradeable_markets": len(current_specs),
            "predictions": predictions, "decisions": decisions, "paper_orders": paper_orders,
        }

    def recommended_poll_seconds(self, now: datetime | None = None) -> int:
        """Increase cadence near empirically observed report minutes."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not self._cadence_minutes:
            return 60
        distance = min(
            min((minute - now.minute) % 60, (now.minute - minute) % 60)
            for minute in self._cadence_minutes
        )
        return 15 if distance <= 5 else 60
