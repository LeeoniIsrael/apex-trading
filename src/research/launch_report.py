"""Small, free launch report for operator monitoring; never enables trading."""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.research.readiness import database_readiness, launch_failures
from src.research.experiments import MODEL_VERSION


def launch_report(database, bankroll, profile='validated'):
    ready, failures, evidence = database_readiness(database, bankroll)
    profile_failures = launch_failures(failures, profile)
    profile_ready = not profile_failures if profile == 'experimental_100' else ready
    with database.connect() as c:
        last_prediction = c.execute('SELECT MAX(predicted_at) FROM model_predictions WHERE model_version=?',(MODEL_VERSION,)).fetchone()[0]
        last_cycle = c.execute("SELECT MAX(metric_date) FROM strategy_metrics WHERE metric_name='service_heartbeat'").fetchone()[0]
        candidates = c.execute('SELECT COUNT(*) FROM research_candidates WHERE model_version=?',(MODEL_VERSION,)).fetchone()[0]
        checks = {r['name']:{'passed':bool(r['passed']),'checked_at':r['checked_at']}
                  for r in c.execute('SELECT name,passed,checked_at FROM verification_checks')}
    now = datetime.now(timezone.utc)
    fresh = bool(last_cycle and 0 <= (now-datetime.fromisoformat(last_cycle)).total_seconds() < 900)
    # A missing/stale worker must not appear ready merely because past evidence passed.
    blockers = list(profile_failures)
    if not fresh:
        blockers.append('research_worker_not_fresh')
    return {'checked_at':now.isoformat(),'model_version':MODEL_VERSION,
            'ready':profile_ready and fresh,'operator_action':'review_separate_activation' if profile_ready and fresh else 'do_not_fund_yet',
            'validation_profile':profile, 'validated_readiness_passed':ready,
            'research_requirements_waived':[f for f in failures if f not in profile_failures],
            'blockers':blockers,'evidence':asdict(evidence),'candidate_snapshots':candidates,
            'last_prediction':last_prediction,'last_cycle':last_cycle,'checks':checks,
            'note':'Funding never enables trading. No live marker is created by this report.'}


def write_launch_report(database, bankroll, profile='validated'):
    result = launch_report(database, bankroll, profile)
    target = database.path.parent/'launch-readiness.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True))
    temporary.replace(target)
    return result
