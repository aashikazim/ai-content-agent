"""Application-Python coordinator; model imports stay in isolated GPU children."""
import sys
import time


def main(db_path, identifier):
    from app.services.cloud_video_service import CloudVideoService
    from app.services.local_production_runner import LocalProductionRunner
    service = CloudVideoService(db_path)
    runner = LocalProductionRunner(service)
    try:
        while runner.tick(identifier):
            time.sleep(5)
    except Exception:
        with service.storage.connect() as conn:
            conn.execute("UPDATE local_production_runs SET status='stopped',error='Coordinator stopped; inspect existing task IDs before manually continuing' WHERE run_id=?", (identifier,))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
