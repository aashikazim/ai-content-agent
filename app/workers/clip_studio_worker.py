"""Detached application-Python queue; no GPU or paid model imports."""
import fcntl
import os
import sys


def main(db_path):
    from app.services.clip_studio.service import ClipStudioService
    os.umask(0o077)
    service = ClipStudioService(db_path)
    # Blocking lock prevents the empty-queue/launcher race. Requests are bounded to20.
    with (service.root/'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        while service.run_next():
            pass


if __name__ == '__main__':
    main(sys.argv[1])
