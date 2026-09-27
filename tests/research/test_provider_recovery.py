from datetime import datetime,timezone
from src.storage.database import Database
from src.research.readiness import database_readiness


def test_provider_failure_requires_later_station_recovery_without_erasing_history(tmp_path):
    db=Database(tmp_path/'paper');db.migrate()
    def event(code,message,severity='error'):
        with db.transaction() as c:
            c.execute('INSERT INTO health_events(occurred_at,severity,component,code,message,details_json) VALUES(?,?,?,?,?,?)',
                      (datetime.now(timezone.utc).isoformat(),severity,'weather_provider',code,message,'{}'))
    event('fetch_failed','KPHX: RuntimeError')
    assert database_readiness(db)[2].major_data_incidents==1
    event('fetch_recovered','KPHX','info')
    assert database_readiness(db)[2].major_data_incidents==0
    event('fetch_failed','KPHX: RuntimeError')
    assert database_readiness(db)[2].major_data_incidents==1
    event('fetch_recovered','KPHX','info')
    event('settlement_parser_failure','KDEN')
    assert database_readiness(db)[2].major_data_incidents==1
