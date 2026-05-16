"""Weekly SQLite snapshot → DO Spaces, rotating to the newest N."""

from __future__ import annotations

import asyncio
import gzip
import io
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog

from ceo_bot import storage
from ceo_bot.config import settings

log = structlog.get_logger()

BACKUP_PREFIX = "backups/"
KEEP_LAST_N = 3


def _snapshot_to_gzip_bytes() -> bytes:
    src = sqlite3.connect(str(settings.database_path))
    try:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            dst = sqlite3.connect(tmp.name)
            try:
                src.backup(dst)
            finally:
                dst.close()
            raw = Path(tmp.name).read_bytes()
    finally:
        src.close()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        gz.write(raw)
    return buf.getvalue()


def _rotate() -> int:
    client = storage._client()
    resp = client.list_objects_v2(
        Bucket=settings.do_spaces_bucket, Prefix=BACKUP_PREFIX
    )
    objs = resp.get("Contents", [])
    if len(objs) <= KEEP_LAST_N:
        return 0
    objs.sort(key=lambda o: o["Key"], reverse=True)
    stale = objs[KEEP_LAST_N:]
    client.delete_objects(
        Bucket=settings.do_spaces_bucket,
        Delete={"Objects": [{"Key": o["Key"]} for o in stale]},
    )
    return len(stale)


def _upload(key: str, blob: bytes) -> None:
    storage._client().put_object(
        Bucket=settings.do_spaces_bucket,
        Key=key,
        Body=blob,
        ContentType="application/gzip",
    )


async def run_weekly_backup() -> None:
    log.info("backup.start")
    try:
        blob = await asyncio.to_thread(_snapshot_to_gzip_bytes)
        key = f"{BACKUP_PREFIX}bot-{datetime.now(UTC).strftime('%Y-%m-%dT%H-%M-%SZ')}.db.gz"
        await asyncio.to_thread(_upload, key, blob)
        rotated = await asyncio.to_thread(_rotate)
        log.info("backup.done", key=key, bytes=len(blob), rotated=rotated)
    except Exception:
        log.exception("backup.failed")


if __name__ == "__main__":
    asyncio.run(run_weekly_backup())
