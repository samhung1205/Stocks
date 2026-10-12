"""Create a consistent, verified SQLite snapshot (including committed WAL data).

Usage: python scripts/backup_sqlite.py SOURCE DESTINATION
Refuses missing/placeholder input and refuses to overwrite an existing backup.
"""
import argparse
import os
from pathlib import Path
import sqlite3
import time


def backup(source: Path, destination: Path) -> None:
    with source.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise ValueError("Source is not a readable SQLite database; download iCloud files first")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental overwrites of an earlier snapshot.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    deadline = time.monotonic() + 300

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("Backup did not finish within five minutes")

    src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst, pages=1024, progress=progress)
        result = dst.execute("PRAGMA integrity_check").fetchall()
        if result != [("ok",)]:
            raise RuntimeError("Snapshot integrity check failed")
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
        src.close()
    destination.chmod(0o600)
    print(f"Verified snapshot: {destination} ({destination.stat().st_size} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup(args.source, args.destination)
