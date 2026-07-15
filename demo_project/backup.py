"""Snapshot backups of library.db. Keep the last 20 + one daily rollup for 30 days."""
import os
import glob
import shutil
from datetime import datetime, timedelta

from db import DB_PATH, DATA_DIR

BACKUP_DIR = os.path.join(DATA_DIR, 'backups')


def snapshot(reason: str = '') -> str:
    if not os.path.exists(DB_PATH):
        return ''
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(BACKUP_DIR, f'library-{ts}.db')
    shutil.copy2(DB_PATH, dest)
    _prune()
    return dest


def _prune(keep_recent: int = 20, daily_days: int = 30):
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, 'library-*.db')))
    if len(files) <= keep_recent:
        return
    recent = set(files[-keep_recent:])
    # keep one (the newest) per day within daily_days; drop the rest
    cutoff = datetime.now() - timedelta(days=daily_days)
    seen_days, keep = set(), set(recent)
    for f in reversed(files):
        stamp = os.path.basename(f)[len('library-'):-len('.db')]
        try:
            dt = datetime.strptime(stamp, '%Y%m%d-%H%M%S')
        except ValueError:
            keep.add(f); continue
        day = dt.date()
        if dt >= cutoff and day not in seen_days:
            seen_days.add(day); keep.add(f)
    for f in files:
        if f not in keep:
            try:
                os.remove(f)
            except OSError:
                pass


if __name__ == '__main__':
    print('snapshot:', snapshot('manual'))
