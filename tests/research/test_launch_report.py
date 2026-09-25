import json
from src.storage.database import Database
from src.research.launch_report import write_launch_report


def test_empty_or_stale_system_is_never_reported_ready(tmp_path):
    db=Database(tmp_path/'paper.sqlite3');db.migrate()
    r=write_launch_report(db,100)
    assert not r['ready']
    assert r['operator_action']=='do_not_fund_yet'
    assert 'research_worker_not_fresh' in r['blockers']
    assert 'live_recovery_verified' in r['blockers']
    assert 'live_monitoring_verified' in r['blockers']
    assert 'fee_schedule_verified' in r['blockers']
    assert json.loads((tmp_path/'launch-readiness.json').read_text())==r
    assert not (tmp_path/'LIVE_ENABLED').exists()
