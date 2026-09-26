import json
from datetime import datetime,timedelta,timezone
from src.research.forecast_error import error_summary
from src.storage.database import Database
from src.strategy.settlement import parse_settlement_spec


def test_final_twc_errors_use_only_before_day_forecasts(tmp_path):
    db=Database(tmp_path/'research');db.migrate()
    today=datetime.now(timezone.utc).date()-timedelta(days=3)
    ticker='KXLOWTLAX-'+today.strftime('%y%b%d').upper()+'-B68.5'
    raw={'ticker':ticker,'event_ticker':ticker.rsplit('-',1)[0],
         'series_ticker':'KXLOWTLAX',
         'rules_primary':f'If the minimum temperature recorded at Los Angeles (CLILAX) for {today.strftime("%b %d, %Y")}, is between 68-69° fahrenheit according to The Weather Company, then the market resolves to Yes.'}
    spec=parse_settlement_spec(raw)
    assert spec.tradeable
    start=spec.observation_window_start
    with db.transaction() as c:
        c.execute('INSERT INTO markets VALUES(?,?,?,?,?,?)',(ticker,raw['event_ticker'],'KXLOWTLAX','settled',json.dumps(raw),start.isoformat()))
        c.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?,?)',(ticker,69,1,'weather_company',1,(start+timedelta(days=2)).isoformat(),'{}'))
        c.execute('INSERT INTO forecast_runs(station_id,provider,model,generated_at_utc,ingested_at_utc,raw_json) VALUES(?,?,?,?,?,?)',
                  ('KLAX','Open-Meteo','test',(start-timedelta(hours=1)).isoformat(),(start-timedelta(hours=1)).isoformat(),'{}'))
        run=c.execute('SELECT id FROM forecast_runs').fetchone()[0]
        for hour in range(24):
            c.execute('INSERT INTO weather_forecasts(forecast_run_id,member,valid_at_utc,temperature_f,features_json) VALUES(?,?,?,?,?)',
                      (run,0,(start+timedelta(hours=hour)).isoformat(),67,'{}'))
    result=error_summary(db)
    assert result['training_days']+result['holdout_days']==1
    assert result['forecast_error_std_f']==4.0
    assert error_summary(db,start+timedelta(hours=1))['training_days']==0
