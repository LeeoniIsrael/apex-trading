import json
import pytest
from src.research.readiness import launch_failures,EXPERIMENTAL_RESEARCH_WAIVERS
from src.weather_config import WeatherSettings
from tests.execution.test_live_executor import setup


def test_research_waiver_never_waives_unknown_or_technical_failures():
    technical=('accounting_verified','authenticated_balance_verified','idempotency_verified',
               'emergency_stop_verified','telegram_alerts_verified','equity_reporting_verified',
               'live_recovery_verified','live_monitoring_verified','fee_schedule_verified',
               'nonfinite_evidence','settlement_parser_failures','data_quality_incidents','future_unknown_check')
    failures=tuple(EXPERIMENTAL_RESEARCH_WAIVERS)+technical
    assert launch_failures(failures,'experimental_100')==technical
    assert launch_failures(failures)==failures
    with pytest.raises(ValueError): launch_failures(failures,'typo')


@pytest.mark.parametrize('field,value',[('live_max_order_usd',2.01),('live_max_exposure_usd',10.01),
    ('live_max_city_exposure_usd',5.01),('live_max_daily_loss_usd',2.01),('live_max_drawdown',.101),
    ('live_max_open_positions',4),('live_max_daily_orders',11),('live_balance_floor_usd',19)])
def test_experimental_limits_cannot_expand(field,value):
    with pytest.raises(ValueError):
        WeatherSettings(_env_file=None,live_validation_profile='experimental_100',**{field:value})


def test_profile_cannot_reuse_validated_marker_or_waive_technical_failure(tmp_path,monkeypatch):
    ex,client,paper,live,settings=setup(tmp_path,monkeypatch)
    settings.live_validation_profile='experimental_100'
    with pytest.raises(RuntimeError,match='validation profile'): ex._gate()
    marker=json.loads(settings.live_enablement_path.read_text())
    marker.update(validation_profile='experimental_100',unvalidated_strategy_acknowledged=True)
    settings.live_enablement_path.write_text(json.dumps(marker))
    monkeypatch.setattr('src.execution.live_executor.database_readiness',
        lambda *a:(False,tuple(EXPERIMENTAL_RESEARCH_WAIVERS),None))
    assert ex._gate()==100
    monkeypatch.setattr('src.execution.live_executor.database_readiness',
        lambda *a:(False,('insufficient_resolved_markets','accounting_verified'),None))
    with pytest.raises(RuntimeError,match='accounting_verified'): ex._gate()
    assert not client.calls


@pytest.mark.parametrize('ack,technical_failure',[(False,False),(True,True),(True,False)])
def test_cli_requires_ack_and_technical_checks_before_writing_marker(tmp_path,monkeypatch,ack,technical_failure):
    import sys
    import src.weather_cli as cli
    ex,client,paper,live,settings=setup(tmp_path,monkeypatch)
    settings.live_enablement_path.unlink()
    settings.live_validation_profile='experimental_100'
    settings.kalshi_api_key_id='test-only'
    settings.kalshi_private_key_path=tmp_path/'not-a-real-key'
    monkeypatch.setattr(cli,'WeatherSettings',lambda **kw:settings)
    failures=tuple(EXPERIMENTAL_RESEARCH_WAIVERS)+(('emergency_stop_verified',) if technical_failure else ())
    monkeypatch.setattr(cli,'_readiness',lambda *a:(False,failures,None))
    monkeypatch.setattr('src.kalshi.auth.KalshiSigner',lambda *a:object())
    monkeypatch.setattr('src.execution.live_executor.authenticated_balance',lambda *a:100)
    argv=['apex-weather','enable-live','--confirm','I_ACCEPT_LIVE_RISK']
    if ack: argv.append('--accept-unvalidated-strategy')
    monkeypatch.setattr(sys,'argv',argv)
    if not ack or technical_failure:
        with pytest.raises(SystemExit): cli.main()
        assert not settings.live_enablement_path.exists()
    else:
        assert cli.main()==0
        marker=json.loads(settings.live_enablement_path.read_text())
        assert marker['validation_profile']=='experimental_100'
        assert marker['unvalidated_strategy_acknowledged'] is True
    assert not client.calls


def test_pilot_report_preserves_missing_research_evidence(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    from src.storage.database import Database
    import src.research.launch_report as reports
    from src.research.readiness import database_readiness
    db=Database(tmp_path/'research');db.migrate()
    evidence=database_readiness(db)[2]
    with db.transaction() as c:
        c.execute("INSERT INTO strategy_metrics(metric_date,metric_name,metric_value,dimensions_json) VALUES(?,'service_heartbeat',1,'{}')",(datetime.now(timezone.utc).isoformat(),))
    failures=('insufficient_resolved_markets','calibration_gate')
    monkeypatch.setattr(reports,'database_readiness',lambda *a:(False,failures,evidence))
    r=reports.launch_report(db,100,'experimental_100')
    assert r['ready'] and not r['validated_readiness_passed']
    assert r['research_requirements_waived']==list(failures)
    assert r['evidence']['resolved_markets']==0
