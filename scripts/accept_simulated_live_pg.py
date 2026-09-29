#!/usr/bin/env python3
"""PG-backed simulated incoming telemetry smoke acceptance on a TEST database.

Requires TGOAT_OPERATIONAL_DATABASE_URL set to a disposable PostgreSQL 12+
instance. Explicit --advance is required because this writes live-test progress.
Run this script twice in separate processes to check restart/watermark continuity.
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--advance',action='store_true',help='write exactly one 5-minute tick in test PG database')
    parser.add_argument('--expected-covered',type=int,default=77)
    args=parser.parse_args()
    if not os.getenv('TGOAT_OPERATIONAL_DATABASE_URL'):
        parser.error('Use dedicated TEST TGOAT_OPERATIONAL_DATABASE_URL; absent PG URL')
    if not args.advance:
        parser.error('Explicit --advance required; this modifies PostgreSQL test state')
    from backend.app.services.runtime_ml import build_postgres_operational_runtime
    from backend.app.services.simulated_live import SimulatedLiveOperationalService
    runtime=build_postgres_operational_runtime()
    try:
        service=SimulatedLiveOperationalService(runtime['database'], runtime)
        before=service.status()
        item=service.process_one()
        after=service.status()
        assert after['source_sha256']==before['source_sha256']
        assert after['processed_boundaries']==before['processed_boundaries']+(item['status']=='processed')
        if item['status']=='processed':
            assert item['scored_objects']==args.expected_covered
            assert after['processed_at']==item['processed_at']
            with runtime['database'].connection() as connection:
                rows=connection.execute('''SELECT COUNT(*) FROM prediction_history
                   WHERE snapshot_ts=%s AND object_id = ANY(%s)''',
                   (datetime.fromisoformat(item['processed_at']),service.covered_ids)).fetchone()[0]
                assert rows==args.expected_covered, f'Expected {args.expected_covered} committed predictions, got {rows}'
                duplicates=connection.execute('''SELECT count(*) FROM
                    (SELECT object_id,snapshot_ts,model_version,count(*) AS c FROM prediction_history
                    GROUP BY 1,2,3 HAVING count(*)>1) s''').fetchone()[0]
                assert duplicates==0, 'Prediction identity must be unique'
                count=connection.execute('''SELECT count(*) FROM simulated_live_events
                    WHERE source_id=%s AND timestamp >%s''',
                    (service.source_id,datetime.fromisoformat(item['processed_at']))).fetchone()[0]
                assert count==0, 'Committed event journal contains future observations'
        evidence={'result':'PASS','before':before,'tick':item,'after':after,
                  'verified_prediction_cardinality':args.expected_covered}
        print(json.dumps(evidence,ensure_ascii=False,indent=2))
    finally:
        runtime['database'].close()

if __name__=='__main__':main()
